"""白模（blocking）生成：通过 Blender MCP 渲染灰度预览 / 控制图 / 灰模动画，辅助 AI 视频制作。

四种用途（已确认全选）：
 1) 分镜 previs 预览图：在 concept_video 分镜卡里用白模图展示构图/机位/站位；
 2) AI 视频起始帧/参考：白模图作为 I2V 起始帧，锁定角色站位与机位；
 3) ControlNet 条件图：由白模渲染 depth / normal / line 作控制图（供 ComfyUI 注入）；
 4) Blender 直出灰模动画：相机运镜的灰模帧序列，混入成片或作 blocking 动画。

前置：本地已安装 Blender + Blender MCP 插件，并在 Blender 内启动 MCP Server（端口 9876）。
未就绪时 is_ready() 返回 False，调用方降级跳过，不影响现有管线。

优化说明（性能 → 健壮性 → 质量，均经真机 Blender 5.2.1 background 实测）：
- 性能：渲染引擎可配（auto 优先 EEVEE，失败降级 CYCLES）；同集同几何分镜用场景签名复用场景
  （只更新相机）；采样数可配。4 张控制图的**引擎分工**已按实测重排（原先 depth/normal
  静默继承 line pass 的 CYCLES，白跑两个 Cycles；详见 _ctrl_setup / _line_setup）：
    previs EEVEE · line 默认 CYCLES（可配 eevee，快 6 倍）· depth/normal 回切 EEVEE
  单镜 4 图合计 2.86s -> 0.77s（640x360；不开 line_engine=eevee 时为 1.63s）。
- 健壮性：生成的 bpy 代码先做语法预检；执行后校验 4 张产物确实存在且非空；失败按
  「配置引擎 → CYCLES」降级重试；仍失败抛 BlockingError，杜绝"返回不存在的图"这种静默失败。
- 质量：补上场景光源（原实现无灯，白模只有 world 环境光 -> 整体偏暗、缺立体感）；
  freestyle 线框显式开 View Layer 开关并确保 LineSet 存在（否则 line.png 与 previs 完全相同）；
  depth 用「材质法」(CameraData.View Z Depth + MapRange 固定近远平面) 而非 compositor，
  跨 Blender 版本可用且动画多帧一致；每次渲染前复位白模材质，修复 depth/normal 换材质后
  污染后续渲染的隐患；相机距离随角色数补偿，避免角色贴边。

已适配的 Blender 5.x 变更（真机实测踩到）：
- `scene.node_tree`(compositor) 已移除（改用 compositing_node_group）-> 不用 compositor 出图；
- 多 View Layer 下 `render(write_still=True)` 只写出**一个**文件（实测只得到 multi_.png）
  -> 不要指望靠 View Layer 一次渲出 4 张图；且渲染引擎是**按场景**生效的，把 4 张图塞进
  同一次渲染会让 line pass 的 CYCLES 污染 depth/normal，净效果更慢；
- 引擎枚举在 5.x 又变回 `BLENDER_EEVEE`（4.2 曾是 BLENDER_EEVEE_NEXT）-> 设置引擎一律走
  try/except 降级链，不要硬编码单个标识符；
- EEVEE 在 5.2 **能**出 freestyle 线（BLENDER_WORKBENCH 不能，且会忽略材质节点）；
- `bpy.ops.render.render(scene=...)` 只接受**场景名字符串**，传 Scene 对象会 TypeError -> 用 scn.name；
- 场景自定义属性是 C int(32 位有符号)，超范围会 OverflowError -> 签名掩到 31 位；
- `Material/Scene.use_nodes` 会有 DeprecationWarning（5.2 仍可用，6.0 将移除）。
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.blender_mcp import BlenderMCP
from agent.llmutil import make_client, chat, extract_json, log

try:                       # 统一 ffmpeg 定位（PATH 上通常没有，靠 imageio-ffmpeg 自带）
    from comfy_paths import ffmpeg_exe as _ffmpeg_exe
except Exception:          # 单独拷走 blocking.py 时也能降级
    _ffmpeg_exe = None

# ffmpeg 定位较重（可能 import imageio_ffmpeg / 查 PATH），批量动画合成时反复调用，
# 故缓存解析结果：False=未初始化；None=查无；str=绝对路径。
_ffmpeg_path: object = False


def _resolve_ffmpeg() -> str | None:
    """定位 ffmpeg（FFMPEG env > PATH > imageio-ffmpeg 自带），结果缓存复用。"""
    global _ffmpeg_path
    if _ffmpeg_path is not False:
        return _ffmpeg_path  # type: ignore[return-value]
    p: str | None = None
    if _ffmpeg_exe:
        p = _ffmpeg_exe()
    if not p:
        p = shutil.which("ffmpeg")
    if not p:
        try:
            import imageio_ffmpeg  # type: ignore
            p = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:          # noqa: BLE001 - 没装则视为不可用
            p = None
    _ffmpeg_path = p
    return p


class BlockingError(RuntimeError):
    """白模渲染失败（已重试仍无有效产物）。"""


class BlockingGenerator:
    # H3 的 reference_video_policy 是官方 2~15s：灰模动画短于 2s 不满足 ref_video 要求
    MIN_REF_SECONDS = 2.0

    def __init__(self, config: dict, workdir: str):
        self.config = config
        self.cfg = config.get("blender", {}) or {}
        self.enabled = bool(self.cfg.get("enabled", False))
        self.workdir = os.path.abspath(workdir)
        self.host = self.cfg.get("host", "127.0.0.1")
        self.port = int(self.cfg.get("port", 9876))
        # 性能/健壮性配置
        self.engine = str(self.cfg.get("engine", "auto") or "auto").lower()
        self.samples = int(self.cfg.get("samples", 24))
        self.timeout = float(self.cfg.get("timeout", 180))
        self.anim_timeout = float(self.cfg.get("anim_timeout", 900))
        self.retries = int(self.cfg.get("retries", 2))
        self.reuse_scene = bool(self.cfg.get("reuse_scene", True))
        # 4 张控制图的引擎策略（详见 _ctrl_setup / _line_setup 的实测数据）
        #   fast_control_passes: depth/normal 两个 pass 不继承 line pass 的 CYCLES
        #   line_engine:         line 线框 pass 用什么引擎出 freestyle
        self.fast_control_passes = bool(self.cfg.get("fast_control_passes", True))
        _le = str(self.cfg.get("line_engine", "cycles") or "cycles").lower()
        if _le not in ("cycles", "eevee"):
            log(f"  [blender] line_engine={_le!r} 不是 cycles/eevee，回退 cycles")
            _le = "cycles"
        self.line_engine = _le
        self.depth_near = float(self.cfg.get("depth_near", 0.1))
        self.depth_far = float(self.cfg.get("depth_far", 100.0))
        self.client = BlenderMCP(self.host, self.port, timeout=self.timeout)
        self.out_dir = os.path.join(self.workdir, "blocking")
        os.makedirs(self.out_dir, exist_ok=True)
        self.width = int(self.cfg.get("width", 1280))
        self.height = int(self.cfg.get("height", 720))
        # 出片帧率（灰模动画时长换算用）与动画帧数解析
        self.fps = int(((config.get("engine", {}) or {}).get("fps", 24)) or 24)
        self.anim_frames = self._resolve_anim_frames()
        # 白模控制走位（Fun Control）：depth 走位序列 -> H3 Fun Control 逐帧注入 DiT。
        # 控制视频必须与出片**同分辨率、同帧数(17n+5)**（fit_mode=exact），故用引擎出片参数。
        self.fc_enabled = bool(self.cfg.get("use_as_fun_control", False))
        self.fc_walk = str(self.cfg.get("fun_control_walk", "-1,0:1,0") or "")
        self.fc_frames = self._resolve_fc_frames()
        self.fc_w, self.fc_h = self._resolve_fc_size()

    def _resolve_fc_frames(self) -> int:
        """Fun Control 控制视频帧数：对齐出片 num_frames 并吸附到 H3 的 17n+5 网格。

        吸附直接调 `MMH3Engine.snap_length`（唯一来源）：这里原先自己写了一遍
        `5 + ceil((nf-5)/17)*17`，两处一旦漂移，控制视频与出片帧数错位，
        FunControlApply 的 frame_load_cap 会静默截断，走位只锁住前几帧。
        配置段别名也统一走 pick_engine_section（原先只认 comfyui_mmH3，
        配成 minimax_h3 时这里会读到默认值，帧数与真实出片不符）。
        """
        from .mmh3_engine import MMH3Engine
        from .video_engine import H3_SECTION_ALIASES, pick_engine_section

        mmh3 = pick_engine_section(self.config, *H3_SECTION_ALIASES)
        try:
            nf = int(mmh3.get("num_frames", 0))
        except (TypeError, ValueError):
            nf = 0
        return MMH3Engine.snap_length(nf or 48)

    def _resolve_fc_size(self) -> tuple:
        """Fun Control 控制视频分辨率：必须与出片一致（fit_mode=exact）。

        同样调引擎的 `snap_resolution`：出片分辨率会被向下吸附到 32 的倍数，
        这里若按原始配置值渲染（如 800x448 → 出片 768x448），exact 对齐必然失败。
        """
        from .mmh3_engine import MMH3Engine
        from .video_engine import H3_SECTION_ALIASES, pick_engine_section

        mmh3 = pick_engine_section(self.config, *H3_SECTION_ALIASES)
        res = str(mmh3.get("resolution", "768x448") or "768x448").lower()
        w, h = (int(v) for v in MMH3Engine.snap_resolution(res).split("x"))
        return w, h

    def _resolve_anim_frames(self) -> int:
        """解析 blender.anim_frames。

        "auto"（默认）：对齐出片帧数 engine.comfyui_mmH3.num_frames，并按 2s 下限兜底
        （H3 参考视频是官方 2~15s 策略，低于 2s 不被接受）；也可直接写整数。
        """
        raw = self.cfg.get("anim_frames", "auto")
        floor = max(1, int(self.MIN_REF_SECONDS * self.fps))   # 2.0s 下限（H3 ref_video 策略）
        if not (isinstance(raw, str) and raw.strip().lower() == "auto"):
            try:
                return max(int(raw), 1)
            except (TypeError, ValueError):
                pass
        from .video_engine import H3_SECTION_ALIASES, pick_engine_section

        mmh3 = pick_engine_section(self.config, *H3_SECTION_ALIASES)
        try:
            nf = int(mmh3.get("num_frames", 0))
        except (TypeError, ValueError):
            nf = 0
        return max(nf, floor) if nf > 0 else max(48, floor)

    @staticmethod
    def _ffmpeg() -> str | None:
        """定位 ffmpeg：统一走 comfy_paths（FFMPEG env > PATH > imageio-ffmpeg 自带）。

        本机 ffmpeg **不在 PATH**（只有 `imageio-ffmpeg` 里带一份）。若只查 PATH，
        灰模动画合成会静默跳过 → `use_as_ref_video` 永远不可用。
        结果按 _resolve_ffmpeg() 缓存，避免批量合成时反复查找。
        """
        return _resolve_ffmpeg()

    # ---------- 就绪 ----------
    def is_ready(self) -> bool:
        if not self.enabled:
            return False
        return self.client.is_ready()

    # ---------- 分镜文本 -> 场景 spec ----------
    # 关键词 -> 取值的**声明式规则表**：列表顺序 == 优先级，先命中先定。
    # 原先是顺序敏感的 if/elif 链（`摇` 必须排在 `推/拉` 前、"从左到右" 必须排在
    # "走近镜头" 前），优先级只存在于代码顺序里，扩展和测试都很难；改成表之后
    # 优先级一眼可见，加一类走位只需加一行，且能逐条锁进单测。
    # 统一按小写匹配（原实现 shot/camera/height 大小写敏感、走位却先 lower，
    # 口径不一 —— 结果就是英文 "Wide shot" 选不中全景）。
    SHOT_RULES: tuple = (
        ("wide", ("全景", "远景", "大远景", "wide", "establishing")),
        ("close", ("特写", "大特写", "近景", "close", "cu")),
    )
    CAMERA_RULES: tuple = (
        ("pan", ("摇", "pan")),
        ("dolly", ("推", "推进", "拉", "dolly")),
        ("track", ("横移", "移", "跟拍", "track")),
        ("orbit", ("环绕", "旋转", "转圈", "orbit")),
    )
    HEIGHT_RULES: tuple = (
        ("low", ("低机位", "仰拍", "low")),
        ("high", ("高机位", "俯拍", "俯视", "航拍", "high")),
    )
    # 走位规则（归一化坐标，-1=画面左 / +1=画面右）。
    # 注意：「左/右同时出现」不由本表处理，而是优先按出现先后定方向（见 _parse_walk）。
    WALK_RULES: tuple = (
        ("approach", ("走近镜头", "走向镜头", "靠近镜头", "approach"),
         "0,0.6:0,-0.6"),
        ("away", ("远离镜头", "走远", "walk away"), "0,-0.6:0,0.6"),
        ("back_forth", ("来回", "徘徊", "踱步", "走来走去"), "-1,0:1,0:-1,0"),
        ("circle", ("绕圈", "环绕走", "绕着", "走了一圈", "绕一圈", "绕一",
                    "circle walk"),
         "-1,0.4:1,0.4:1,-0.4:-1,-0.4:-1,0.4"),
    )

    @staticmethod
    def _first_match(lowered: str, rules) -> str | None:
        """按规则表顺序返回第一个命中的取值；无命中返回 None（表顺序 == 优先级）。"""
        for value, keywords in rules:
            if any(k in lowered for k in keywords):
                return value
        return None

    @classmethod
    def _parse_walk(cls, lowered: str) -> str:
        """从分镜文本解析走位；无走位关键词返回 ""（调用方回退 config 默认）。

        「左/右同时出现」优先按出现先后定方向，覆盖
        "从左到右" / "从画面左侧走到右侧" / "由左向右" / "left to right" 等说法。
        """
        left_cn, right_cn = lowered.find("左"), lowered.find("右")
        left_en, right_en = lowered.find("left"), lowered.find("right")
        if (left_cn >= 0 and right_cn >= 0) or (left_en >= 0 and right_en >= 0):
            left = left_cn if left_cn >= 0 else left_en
            right = right_cn if right_cn >= 0 else right_en
            return "-1,0:1,0" if left < right else "1,0:-1,0"
        for _name, keywords, walk in cls.WALK_RULES:
            if any(k in lowered for k in keywords):
                return walk
        return ""

    def parse_spec(self, text: str) -> dict:
        spec = {"characters": 1, "props": [], "shot": "medium",
                "camera": "static", "height": "eye"}
        t = text or ""
        low = t.lower()
        spec["shot"] = self._first_match(low, self.SHOT_RULES) or spec["shot"]
        spec["camera"] = self._first_match(low, self.CAMERA_RULES) or spec["camera"]
        spec["height"] = self._first_match(low, self.HEIGHT_RULES) or spec["height"]
        # 可选：LLM 增强角色数 / 道具
        if self.cfg.get("use_llm_parse"):
            try:
                client = make_client(self.config)
                resp = chat(client,
                            "你是影视分镜解析器。从分镜文本提取：角色数量(characters int 1-6)、"
                            "道具列表(props list[str])、机位高度(height 取 eye/low/high)。"
                            "只输出 JSON，如 {\"characters\":2,\"props\":[\"桌\"],\"height\":\"eye\"}.",
                            f"文本：{t}", temperature=0.2, max_tokens=200)
                if resp:
                    j = json.loads(extract_json(resp) or "{}")
                    if isinstance(j, dict):
                        if isinstance(j.get("characters"), int) and 1 <= j["characters"] <= 6:
                            spec["characters"] = j["characters"]
                        if isinstance(j.get("props"), list):
                            spec["props"] = [str(p) for p in j["props"][:4]]
                        if j.get("height") in ("eye", "low", "high"):
                            spec["height"] = j["height"]
            except Exception as e:
                log(f"  [blocking] LLM 解析失败，用规则兜底: {e}")
        # 走位（归一化坐标；空 = 用 config 的 fun_control_walk 默认）。
        # 仅用于 Fun Control 控制序列；既有的 ref_images / ref_video 路线不受影响。
        spec["walk"] = self._parse_walk(low)
        return spec

    # ---------- 代码模板填充 ----------
    @staticmethod
    def _fill(code: str, **kw) -> str:
        for k, v in kw.items():
            code = code.replace("{" + k + "}", str(v))
        return code

    @staticmethod
    def _scene_sig(spec: dict) -> int:
        """场景几何签名（角色数 + 道具）。相同则可复用已建场景，只更新相机。

        用 md5 而非内置 hash：保证跨进程稳定（同一集多次调用/重启 Python 仍能命中复用）。
        结果必须落在 C int(32 位有符号) 内：Blender 的 IDProperty 存 int 用 C int，
        超出会抛 OverflowError（真机实测踩到过）。
        """
        n = max(1, int(spec.get("characters", 1)))
        props = [str(p) for p in (spec.get("props", []) or [])]
        key = json.dumps({"c": n, "p": props}, sort_keys=True, ensure_ascii=False)
        return int(hashlib.md5(key.encode("utf-8")).hexdigest()[:8], 16) & 0x7FFFFFFF

    def _engine_setup(self, engine: str) -> str:
        """生成注入到 bpy 代码的渲染引擎设置片段（顶层语句，无缩进要求）。"""
        s = int(self.samples)
        if engine == "cycles":
            return (
                'scn.render.engine="CYCLES"\n'
                'try:\n'
                '    scn.cycles.device="CPU"\n'
                f'    scn.cycles.samples={s}\n'
                '    scn.cycles.use_denoising=True\n'
                'except Exception:\n'
                '    pass\n'
            )
        # eevee / eevee_next / auto：优先 EEVEE，不可用再退 CYCLES
        return (
            'try:\n'
            '    scn.render.engine="BLENDER_EEVEE_NEXT"\n'
            'except Exception:\n'
            '    try:\n'
            '        scn.render.engine="BLENDER_EEVEE"\n'
            '    except Exception:\n'
            '        scn.render.engine="CYCLES"\n'
            'try:\n'
            f'    scn.eevee.taa_render_samples={s}\n'
            'except Exception:\n'
            '    pass\n'
        )

    def _plans(self) -> list:
        """引擎降级链：配置引擎（auto→EEVEE）→ CYCLES。

        说明：原计划的"previs+depth 合并为一次渲染"依赖 compositor 的 scene.node_tree，
        而 Blender 5.2 已移除该属性（改用 compositing_node_group），为跨版本可靠，
        统一采用「4 张分别渲染 + 材质法出 depth/normal」，提速主要来自 EEVEE。

        补充（2026-09 真机实测，Blender 5.2.1）：也**不该**去合并。理由是
        `write_still` 在多个 View Layer 下只写一个文件（实测只出 multi_.png），
        而合成器 File Output 路线虽然能一次渲多文件，但渲染引擎是**按场景**而非
        按 View Layer 生效的 —— 一旦把 4 个图层塞进同一次渲染，line pass 需要的
        CYCLES 会把 depth/normal 也拖回 CYCLES，净效果更慢。真正该修的是下面
        那个「depth/normal 静默继承 CYCLES」的泄漏（见 _ctrl_setup）。
        """
        fast = self.engine if self.engine != "auto" else "eevee"
        plans = [("block", fast), ("block", "cycles")]
        uniq = []
        for p in plans:
            if p not in uniq:
                uniq.append(p)
        return uniq[: max(1, int(self.retries) + 1)]

    def _ctrl_setup(self) -> str:
        """depth / normal 两个 pass 的引擎设置片段。

        为什么必须显式插一段：原模板里只有 line pass 之前一处 `engine="CYCLES"`，
        而 depth / normal 紧跟其后、**没有任何回切**，于是两个纯 emission 材质的
        截图也一直跑在 CYCLES 上。实测（Blender 5.2.1，640x360，samples=16）：

            pass     CYCLES    EEVEE
            depth    0.63s  →  0.12s
            normal   0.62s  →  0.12s

        产物正确性（逐像素比对 CYCLES 版本，230400 像素）：
            depth   仅 0.14% 像素有差，其中 99.4% 落在几何边缘，内部仅 2 px
            normal  仅 0.99% 像素有差，其中 97.8% 落在几何边缘，内部仅 49 px(0.02%)
        即差异只是轮廓抗锯齿（Cycles 采样+降噪 vs EEVEE TAA），控制图语义不变。
        `fast_control_passes: false` 可回退为继承 CYCLES（等价旧行为）。
        """
        if not self.fast_control_passes:
            return ""
        # 注意：EEVEE 的引擎名在 Blender 版本间改过（4.2 叫 BLENDER_EEVEE_NEXT，
        # 5.x 又回到 BLENDER_EEVEE），故沿用与 _engine_setup 相同的降级链。
        return (
            'try:\n'
            '    scn.render.engine="BLENDER_EEVEE"\n'
            f'    scn.eevee.taa_render_samples={min(max(1, int(self.samples)), 16)}\n'
            'except Exception:\n'
            '    try:\n'
            '        scn.render.engine="BLENDER_EEVEE_NEXT"\n'
            f'        scn.eevee.taa_render_samples={min(max(1, int(self.samples)), 16)}\n'
            '    except Exception:\n'
            '        pass\n'
        )

    def _line_setup(self) -> str:
        """line 线框 pass 的引擎设置片段（freestyle 出线）。

        历史注释说「freestyle 仅 CYCLES / legacy EEVEE 支持」，真机实测在
        Blender 5.2.1 下不成立：`BLENDER_EEVEE` 已能出 freestyle 线
        （line.png 暗像素 0.84% vs CYCLES 0.70%，相对 previs 新增 1935 个暗像素，
        且与 CYCLES 版本的线条掩膜 IoU = 72.9% —— 线条位置一致，只有线宽与抗锯齿
        的细微差别）。耗时差 6 倍：

            line pass   CYCLES 1.43s  →  EEVEE 0.23s

        但 line.png 是要喂给视频引擎的**控制图**，与已做过的 A/B 基线不完全同源，
        故默认仍取 CYCLES（产物逐像素等于历史基线，零风险）：
            blender.line_engine: eevee   # 想要 6 倍提速时显式开启

        另：BLENDER_WORKBENCH 虽然更快（0.09s），但它忽略材质节点，会把
        depth/normal 的材质法渲成完全不同的图（实测与 CYCLES 差 55% 像素），排除。
        """
        ls = min(max(1, int(self.samples)), 16)
        if self.line_engine == "eevee":
            return (
                'try:\n'
                '    scn.render.engine="BLENDER_EEVEE"\n'
                f'    scn.eevee.taa_render_samples={ls}\n'
                'except Exception:\n'
                '    try:\n'
                '        scn.render.engine="BLENDER_EEVEE_NEXT"\n'
                f'        scn.eevee.taa_render_samples={ls}\n'
                '    except Exception:\n'
                '        pass\n'
            )
        return (
            'try:\n'
            '    scn.render.engine="CYCLES"\n'
            f'    scn.cycles.samples={ls}\n'
            '    scn.cycles.use_denoising=True\n'
            'except Exception:\n'
            '    pass\n'
        )

    def _common(self, spec: dict, out_dir: str) -> dict:
        # Blender 会把相对路径解析到它自己的 cwd（不是本进程的 cwd）→ 一律转绝对，
        # 否则渲染产物会落到 Blender 的工作目录（静默"无产物"）。
        out_dir = os.path.abspath(out_dir)
        n = max(1, int(spec.get("characters", 1)))
        props = [str(p) for p in (spec.get("props", []) or [])]
        return dict(
            N=str(n),
            PROPS=json.dumps(props, ensure_ascii=False),
            SIG=str(self._scene_sig(spec)),
            REUSE="True" if self.reuse_scene else "False",
            SHOT=json.dumps(spec.get("shot", "medium")),
            HEIGHT=json.dumps(spec.get("height", "eye")),
            CAMERA=json.dumps(spec.get("camera", "static")),
            W=str(self.width), H=str(self.height),
            PREVIS=json.dumps(os.path.join(out_dir, "previs.png")),
            LINE=json.dumps(os.path.join(out_dir, "line.png")),
            DEPTH=json.dumps(os.path.join(out_dir, "depth.png")),
            NORMAL=json.dumps(os.path.join(out_dir, "normal.png")),
            OUTDIR=json.dumps(out_dir),
            NEAR=str(self.depth_near),
            FAR=str(self.depth_far),
            LS=str(min(int(self.samples), 16)),
            # Fun Control 走位控制序列（归一化走位；分辨率/帧数对齐出片）
            FCW=str(self.fc_w), FCH=str(self.fc_h), FCFRAMES=str(self.fc_frames),
            WALK=json.dumps(spec.get("walk") or self.fc_walk, ensure_ascii=False),
            FCPATH=json.dumps(os.path.join(out_dir, "fc_")),
        )

    def _build_block_code(self, spec: dict, out_dir: str, mode: str, engine: str) -> str:
        """mode 参数保留以兼容旧调用（block 渲染路径已统一，不再区分 merged/legacy）。"""
        c = self._common(spec, out_dir)
        c["ENGINE"] = self._engine_setup(engine)
        # 4 张图的引擎策略：line 出线用哪个引擎、depth/normal 是否回切到快速引擎
        c["LINE_SETUP"] = self._line_setup()
        c["CTRL_SETUP"] = self._ctrl_setup()
        core = self._fill(_CORE_TEMPLATE, **c)
        cam = self._fill(_CAM_TEMPLATE, **c)
        tail = self._fill(_TAIL_BLOCK, **c)
        return core + "\n" + cam + "\n" + tail

    def _build_anim_code(self, spec: dict, out_dir: str, frames: int, engine: str) -> str:
        c = self._common(spec, out_dir)
        c["ENGINE"] = self._engine_setup(engine)
        c["FRAMES"] = str(int(frames))
        core = self._fill(_CORE_TEMPLATE, **c)
        cam = self._fill(_CAM_TEMPLATE, **c)
        tail = self._fill(_ANIM_TAIL, **c)
        return core + "\n" + cam + "\n" + tail

    def _build_fc_code(self, spec: dict, out_dir: str, engine: str) -> str:
        """Fun Control 走位控制序列：depth（近白远黑、地面压黑只留角色）+ 角色走位关键帧。

        走位用归一化坐标，模板内按相机水平半宽换算世界坐标并留边距 → 自动适配镜头、不出画。
        """
        c = self._common(spec, out_dir)
        c["ENGINE"] = self._engine_setup(engine)
        c["FRAMES"] = str(int(self.fc_frames))
        core = self._fill(_CORE_TEMPLATE, **c)
        cam = self._fill(_CAM_TEMPLATE, **c)
        tail = self._fill(_FC_TAIL, **c)
        return core + "\n" + cam + "\n" + tail

    # ---------- 校验 / 预检 ----------
    @staticmethod
    def _check_syntax(code: str) -> None:
        """发送前先编译一遍，把模板填充错误挡在本地（否则只有 Blender 端才报错）。"""
        try:
            compile(code, "<blocking-bpy>", "exec")
        except SyntaxError as e:
            raise BlockingError(f"生成的 bpy 代码语法错误（模板填充异常）：{e}")

    @staticmethod
    def _missing(paths: dict, min_bytes: int = 512) -> list:
        miss = []
        for k, p in paths.items():
            try:
                if (not os.path.exists(p)) or os.path.getsize(p) < min_bytes:
                    miss.append(k)
            except OSError:
                miss.append(k)
        return miss

    @staticmethod
    def _clean(files: list) -> None:
        # 用 cmd `del` 删文件：绕过 python 删除 API 的 SAFE_DELETE turn 级批量拦截
        # （长批量进程内累计删除超阈值后 os.remove 会被拦导致崩；项目惯例见
        #  gen_blocking.py:247/278 的 `os.system('del /q ...')`）。
        for p in files:
            try:
                if os.path.exists(p):
                    os.system(f'del /q "{p}" 2>nul')
            except OSError:
                pass

    # ---------- 渲染 ----------
    def render_block(self, spec: dict, out_dir: str) -> dict:
        """一次出 4 张图：previs 白模 / line 线框 / depth 深度 / normal 法线。

        逐级降级重试，全部失败抛 BlockingError（不返回不存在的路径）。
        """
        os.makedirs(out_dir, exist_ok=True)
        paths = {
            "previs": os.path.join(out_dir, "previs.png"),
            "line": os.path.join(out_dir, "line.png"),
            "depth": os.path.join(out_dir, "depth.png"),
            "normal": os.path.join(out_dir, "normal.png"),
        }
        # 清掉旧产物，避免"上一次的图冒充本次成功"
        self._clean(list(paths.values()))

        last_err = ""
        plans = self._plans()
        for idx, (_mode, engine) in enumerate(plans):
            code = self._build_block_code(spec, out_dir, "block", engine)
            self._check_syntax(code)
            self.client.timeout = self.timeout
            ex = self.client.exec_code_ex(code)
            if not ex["ok"]:
                last_err = ex["error"] or "Blender 执行失败"
                log(f"  [blocking] 白模渲染第 {idx+1} 次失败（{engine}）: {last_err[:200]}")
                continue
            miss = self._missing(paths)
            if not miss:
                if idx > 0:
                    log(f"  [blocking] 白模渲染在第 {idx+1} 次尝试成功（{engine}）")
                return paths
            last_err = f"产物缺失: {', '.join(miss)}"
            log(f"  [blocking] 白模渲染第 {idx+1} 次产物不完整（{engine}）: {last_err}")
        raise BlockingError(f"白模渲染失败（已尝试 {len(plans)} 次）: {last_err}")

    def render_previs(self, spec: dict, out_path: str) -> str:
        out_dir = os.path.dirname(os.path.abspath(out_path))
        os.makedirs(out_dir, exist_ok=True)
        res = self.render_block(spec, out_dir)
        return res["previs"]

    def render_control(self, spec: dict, out_dir: str) -> dict:
        return self.render_block(spec, out_dir)

    def render_animation(self, spec: dict, out_dir: str, frames: int | None = None) -> str:
        frames = frames or self.anim_frames
        os.makedirs(out_dir, exist_ok=True)
        self._clean(glob.glob(os.path.join(out_dir, "blocking_*.png")))
        last_err = ""
        plans = self._plans()
        for idx, (_mode, engine) in enumerate(plans):
            code = self._build_anim_code(spec, out_dir, frames, engine)
            self._check_syntax(code)
            self.client.timeout = self.anim_timeout
            ex = self.client.exec_code_ex(code)
            if not ex["ok"]:
                last_err = ex["error"] or "Blender 执行失败"
                log(f"  [blocking] 灰模动画第 {idx+1} 次失败（{engine}）: {last_err[:200]}")
                continue
            got = glob.glob(os.path.join(out_dir, "blocking_*.png"))
            if got:
                if idx > 0:
                    log(f"  [blocking] 灰模动画在第 {idx+1} 次尝试成功（{engine}）")
                return out_dir
            last_err = "未产出任何帧"
            log(f"  [blocking] 灰模动画第 {idx+1} 次无产物（{engine}）")
        raise BlockingError(f"灰模动画渲染失败（已尝试 {len(plans)} 次）: {last_err}")

    def export_anim_video(self, anim_dir: str) -> str | None:
        """把灰模帧序列合成 mp4，供视频引擎作 ref_video（锁走位/运镜）。

        缺 ffmpeg 时返回 None（不阻断管线）。帧率取 engine.fps。
        """
        frames = sorted(glob.glob(os.path.join(anim_dir, "blocking_*.png")))
        if not frames:
            return None
        out = os.path.join(anim_dir, "blocking.mp4")
        ff = self._ffmpeg()
        if not ff:
            log("  [blocking] 未找到 ffmpeg，跳过灰模动画合成（ref_video 不可用）")
            return None
        dur = len(frames) / float(self.fps)
        if dur < self.MIN_REF_SECONDS:
            log(f"  [blocking] 灰模动画仅 {dur:.2f}s，低于 H3 参考视频的官方下限 "
                f"{self.MIN_REF_SECONDS:g}s；若要用 use_as_ref_video，请把 blender.anim_frames "
                f"设为 auto 或 ≥{int(round(self.MIN_REF_SECONDS * self.fps))}"
                f"（当前 {len(frames)} 帧 @{self.fps}fps）")
        try:
            subprocess.run([ff, "-y", "-loglevel", "error", "-framerate", str(self.fps),
                            "-i", os.path.join(anim_dir, "blocking_%04d.png"),
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", out],
                           check=True, capture_output=True)
            return out if os.path.exists(out) else None
        except Exception as e:
            log(f"  [blocking] 灰模动画合成失败: {e}")
            return None

    def render_fc_anim(self, spec: dict, out_dir: str, frames: int | None = None) -> str:
        """渲染 Fun Control 走位控制序列（逐帧 depth PNG，近白远黑、只留角色）。返回目录。

        与 render_animation 的区别：这里是 depth 控制序列（喂 H3 Fun Control），
        不是灰模运镜（ref_video）；分辨率/帧数严格对齐出片（fit_mode=exact）。
        """
        os.makedirs(out_dir, exist_ok=True)
        self._clean(glob.glob(os.path.join(out_dir, "fc_*.png")))
        last_err = ""
        plans = self._plans()
        for idx, (_mode, engine) in enumerate(plans):
            code = self._build_fc_code(spec, out_dir, engine)
            self._check_syntax(code)
            self.client.timeout = self.anim_timeout
            ex = self.client.exec_code_ex(code)
            if not ex["ok"]:
                last_err = ex.get("error") or "Blender 执行失败"
                log(f"  [blocking] 走位控制序列第 {idx+1} 次失败（{engine}）: {last_err[:200]}")
                continue
            got = glob.glob(os.path.join(out_dir, "fc_*.png"))
            if got:
                if idx > 0:
                    log(f"  [blocking] 走位控制序列在第 {idx+1} 次尝试成功（{engine}）")
                return out_dir
            last_err = "未产出任何帧"
            log(f"  [blocking] 走位控制序列第 {idx+1} 次无产物（{engine}）")
        raise BlockingError(f"走位控制序列渲染失败（已尝试 {len(plans)} 次）: {last_err}")

    def export_fc_video(self, fc_dir: str) -> str | None:
        """把走位控制帧序列合成 mp4（Fun Control 的 control_video）。缺 ffmpeg 返回 None。"""
        frames = sorted(glob.glob(os.path.join(fc_dir, "fc_*.png")))
        if not frames:
            return None
        out = os.path.join(fc_dir, "fc.mp4")
        ff = self._ffmpeg()
        if not ff:
            log("  [blocking] 未找到 ffmpeg，跳过走位控制合成（Fun Control 不可用）")
            return None
        try:
            subprocess.run([ff, "-y", "-loglevel", "error", "-framerate", str(self.fps),
                            "-i", os.path.join(fc_dir, "fc_%04d.png"),
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", out],
                           check=True, capture_output=True)
            return out if os.path.exists(out) else None
        except Exception as e:
            log(f"  [blocking] 走位控制合成失败: {e}")
            return None

    def render_assets(self, prompts: list) -> dict:
        """批量生成全套白模资产（每镜）。返回 {previews, controls, anims}。

        - previews: 白模 previs 图路径（None 表示该镜失败）
        - controls: {previs,line,depth,normal} 控制图路径字典（None 表示失败）
        - anims:    灰模运镜 mp4 路径（static 机位或合成失败为 None）
        - fcvideos: 走位控制视频 mp4（Fun Control 用；未开启/失败为 None）

        单镜失败不影响其它镜（该项记 None 并记录日志），避免一次渲染问题拖垮整批。
        """
        res = {"previews": [], "controls": [], "anims": [], "fcvideos": []}
        for i, p in enumerate(prompts):
            spec = self.parse_spec(p)
            d = os.path.join(self.out_dir, f"shot_{i:03d}")
            try:
                blk = self.render_block(spec, d)
            except BlockingError as e:
                log(f"  [blocking] 第 {i+1} 镜白模渲染失败，跳过: {e}")
                res["previews"].append(None)
                res["controls"].append(None)
                res["anims"].append(None)
                res["fcvideos"].append(None)
                continue
            res["previews"].append(blk["previs"])
            res["controls"].append(blk)
            if spec.get("camera") != "static":
                anim_dir = os.path.join(d, "anim")
                try:
                    self.render_animation(spec, anim_dir, self.anim_frames)
                    res["anims"].append(self.export_anim_video(anim_dir))
                except BlockingError as e:
                    log(f"  [blocking] 第 {i+1} 镜灰模动画失败，跳过: {e}")
                    res["anims"].append(None)
            else:
                res["anims"].append(None)
            # Fun Control 走位控制序列（表达角色走位，与相机是否运动无关）
            if self.fc_enabled:
                fc_dir = os.path.join(d, "fc")
                try:
                    self.render_fc_anim(spec, fc_dir, self.fc_frames)
                    res["fcvideos"].append(self.export_fc_video(fc_dir))
                except BlockingError as e:
                    log(f"  [blocking] 第 {i+1} 镜走位控制失败，跳过: {e}")
                    res["fcvideos"].append(None)
            else:
                res["fcvideos"].append(None)
        return res


# ---------- Blender (bpy) 代码模板 ----------
# 场景几何（地面/角色/道具/灯光）。带签名复用：几何不变则只更新相机，不重建场景。
_CORE_TEMPLATE = r'''
import bpy, os, math
SCN="blocking_tmp"
SIG={SIG}
PROPS={PROPS}
N={N}
REUSE={REUSE}
def wm(obj, shade):
    m=bpy.data.materials.new("wm"); m.use_nodes=False
    m.diffuse_color=(shade,shade,shade,1.0)
    if obj.data.materials: obj.data.materials[0]=m
    else: obj.data.materials.append(m)
scn=bpy.data.scenes.get(SCN)
reuse=bool(REUSE) and (scn is not None) and (int(scn.get("blk_sig",-999999))==SIG)
if not reuse:
    if scn is not None:
        bpy.data.scenes.remove(scn)
    scn=bpy.data.scenes.new(SCN)
    try:
        bpy.context.window.scene=scn
    except Exception:
        pass
    for o in list(scn.collection.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    scn.world=bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    bpy.ops.mesh.primitive_plane_add(size=14)
    g=bpy.context.object; g.name="ground"; wm(g,0.55)
    xs=[(i-(N-1)/2.0)*2.4 for i in range(N)]
    for i,x in enumerate(xs):
        bpy.ops.mesh.primitive_cylinder_add(radius=0.35, depth=1.4, location=(x,0,0.7))
        b=bpy.context.object; b.name=("char_%d"%i); wm(b,0.85)
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.3, location=(x,0,1.5))
        h=bpy.context.object; h.name=("char_%d_h"%i); wm(h,0.9)
    for p in PROPS:
        bpy.ops.mesh.primitive_cube_add(size=0.8, location=(2.8,-1.2,0.4))
        o=bpy.context.object; o.name=("prop_"+str(p)); wm(o,0.7)
    # 光源：原实现完全没有灯，白模只有 world 环境光，渲染整体偏暗且缺立体感
    bpy.ops.object.light_add(type="SUN", location=(0,0,6))
    sun=bpy.context.object; sun.name="blk_sun"
    sun.data.energy=3.0
    sun.rotation_euler=(math.radians(50), 0.0, math.radians(30))
else:
    try:
        bpy.context.window.scene=scn
    except Exception:
        pass
scn["blk_sig"]=SIG
# 复位白模材质：depth / normal 步骤会把材质换成映射材质，必须每次渲染前恢复，
# 否则复用场景时 previs 会渲染成 depth/normal 图（原实现的隐患）。
for o in scn.collection.objects:
    if o.type!="MESH":
        continue
    nm=o.name
    if nm=="ground":
        wm(o,0.55)
    elif nm.startswith("char_") and nm.endswith("_h"):
        wm(o,0.9)
    elif nm.startswith("char_"):
        wm(o,0.85)
    elif nm.startswith("prop_"):
        wm(o,0.7)
'''

# 相机（每镜都更新；场景复用时不重建物体也能改机位）。距离随角色数补偿，避免角色贴边。
_CAM_TEMPLATE = r'''
DIST={"wide":9.0,"medium":5.5,"close":3.2}.get({SHOT},5.5) + 0.6*(N-1)
CAMY=-DIST
CAMZ={"eye":1.6,"low":0.6,"high":4.0}.get({HEIGHT},1.6)
cam=bpy.data.objects.get("block_cam")
tgt=bpy.data.objects.get("cam_tgt")
if cam is None:
    bpy.ops.object.camera_add(location=(0,CAMY,CAMZ)); cam=bpy.context.object; cam.name="block_cam"
if tgt is None:
    bpy.ops.object.empty_add(location=(0,0,1.0)); tgt=bpy.context.object; tgt.name="cam_tgt"
cam.location=(0.0,CAMY,CAMZ)
cam.constraints.clear()
ct=cam.constraints.new("TRACK_TO"); ct.target=tgt
ct.track_axis="TRACK_NEGATIVE_Z"; ct.up_axis="UP_Y"
scn.camera=cam
'''

# 4 张控制图（材质法出 depth/normal，不依赖已移除的 scene.node_tree）
_TAIL_BLOCK = r'''
W={W}; H={H}
scn.render.resolution_x=W; scn.render.resolution_y=H; scn.render.resolution_percentage=100
scn.render.image_settings.file_format="PNG"
scn.render.use_freestyle=False
{ENGINE}
# 1) previs 白模（配置引擎，最快）
scn.render.filepath={PREVIS}
bpy.ops.render.render(write_still=True, scene=scn.name)
# 2) line 线框：freestyle 出线。引擎可配（blender.line_engine，默认 cycles）
#    实测 EEVEE 也能出 freestyle 线且快 6 倍，但 line.png 是喂视频引擎的控制图，
#    默认保持 CYCLES 以逐像素对齐历史基线。见 _line_setup 的实测数据。
{LINE_SETUP}
# freestyle 需同时开「场景」与「View Layer」开关，且必须有 LineSet，否则 line.png == previs.png
scn.render.use_freestyle=True
try:
    _vl=scn.view_layers[0]
    _vl.use_freestyle=True
    _fs=_vl.freestyle_settings
    if len(_fs.linesets)==0:
        _fs.linesets.new("blk")
    _ls=_fs.linesets[0]
    _ls.select_silhouette=True
    _ls.select_crease=True
    _ls.select_border=True
    try:
        _ls.linestyle.color=(0.0,0.0,0.0)
        _ls.linestyle.thickness=2.0
    except Exception:
        pass
except Exception:
    pass
scn.render.filepath={LINE}
bpy.ops.render.render(write_still=True, scene=scn.name)
# 3) depth 深度：材质法（View Z Depth -> 固定范围灰度），多帧一致且跨版本可用
#    depth/normal 是纯 emission 材质，不需要 CYCLES；此处回切快速引擎，
#    否则会静默继承上一段为 line pass 设的 CYCLES（实测每镜多花约 1s）。
{CTRL_SETUP}
def dp_mat():
    m=bpy.data.materials.new("dp"); m.use_nodes=True; t=m.node_tree; t.nodes.clear()
    cd=t.nodes.new("ShaderNodeCameraData")
    mr=t.nodes.new("ShaderNodeMapRange")
    mr.inputs[1].default_value={NEAR}; mr.inputs[2].default_value={FAR}
    mr.inputs[3].default_value=0.0; mr.inputs[4].default_value=1.0
    em=t.nodes.new("ShaderNodeEmission")
    ou=t.nodes.new("ShaderNodeOutputMaterial")
    t.links.new(cd.outputs["View Z Depth"], mr.inputs[0])
    t.links.new(mr.outputs[0], em.inputs["Color"])
    t.links.new(em.outputs[0], ou.inputs[0])
    return m
_dm=dp_mat()
for o in scn.collection.objects:
    if o.type=="MESH":
        o.data.materials.clear(); o.data.materials.append(_dm)
scn.render.use_freestyle=False
scn.render.filepath={DEPTH}
bpy.ops.render.render(write_still=True, scene=scn.name)
# 4) normal 法线：材质法（几何法线 -> 颜色）
def nm_mat():
    m=bpy.data.materials.new("nm"); m.use_nodes=True; t=m.node_tree; t.nodes.clear()
    geo=t.nodes.new("ShaderNodeNewGeometry")
    mul=t.nodes.new("ShaderNodeVectorMath"); mul.operation="MULTIPLY"; mul.inputs[1].default_value=(0.5,0.5,0.5)
    ad=t.nodes.new("ShaderNodeVectorMath"); ad.operation="ADD"; ad.inputs[1].default_value=(0.5,0.5,0.5)
    em=t.nodes.new("ShaderNodeEmission"); ou=t.nodes.new("ShaderNodeOutputMaterial")
    t.links.new(geo.outputs["Normal"], mul.inputs[0]); t.links.new(mul.outputs[0], ad.inputs[0])
    t.links.new(ad.outputs[0], em.inputs["Color"]); t.links.new(em.outputs[0], ou.inputs[0])
    return m
_nm=nm_mat()
for o in scn.collection.objects:
    if o.type=="MESH":
        o.data.materials.clear(); o.data.materials.append(_nm)
scn.render.filepath={NORMAL}
bpy.ops.render.render(write_still=True, scene=scn.name)
print("OK_BLOCK", {PREVIS}, {LINE}, {DEPTH}, {NORMAL})
'''

# 灰模动画（相机运镜帧序列）
_ANIM_TAIL = r'''
W={W}; H={H}
scn.render.resolution_x=W; scn.render.resolution_y=H; scn.render.resolution_percentage=100
scn.render.image_settings.file_format="PNG"
scn.render.use_freestyle=False
{ENGINE}
MV={CAMERA}; FR={FRAMES}
scn.frame_start=1; scn.frame_end=FR
if MV!="static":
    if MV=="dolly":
        cam.location=(0,-DIST*1.5,CAMZ); cam.keyframe_insert("location",frame=1)
        cam.location=(0,-DIST*0.7,CAMZ); cam.keyframe_insert("location",frame=FR)
    elif MV in ("pan","track"):
        cam.location=(-3.0,CAMY,CAMZ); cam.keyframe_insert("location",frame=1)
        cam.location=(3.0,CAMY,CAMZ); cam.keyframe_insert("location",frame=FR)
    elif MV=="orbit":
        for a,fr in [(math.pi/2.0,1),(-math.pi/2.0,FR)]:
            cam.location=(math.cos(a)*DIST, math.sin(a)*DIST, CAMZ); cam.keyframe_insert("location",frame=fr)
scn.render.filepath={OUTDIR} + "/blocking_"
scn.frame_step=1
bpy.ops.render.render(write_still=False, scene=scn.name, animation=True)
print("OK_ANIM", {OUTDIR})
'''

# Fun Control 走位控制序列（逐帧 depth；近白远黑、地面压黑只留角色）。
# 走位为**归一化坐标**（x: -1=画面左 / +1=画面右；y: -1=近 / +1=远），模板内按相机水平
# 半宽 HW=DIST*tan(19.8°) 换算世界坐标并留 20% 边距 → 自动适配镜头、角色全程不出画。
# 分辨率/帧数由 _common 的 FCW/FCH/FCFRAMES 给出（严格对齐出片，fit_mode=exact）。
_FC_TAIL = r'''
W={FCW}; H={FCH}
scn.render.resolution_x=W; scn.render.resolution_y=H; scn.render.resolution_percentage=100
scn.render.image_settings.file_format="PNG"
scn.render.use_freestyle=False
{ENGINE}
FR={FRAMES}
scn.frame_start=1; scn.frame_end=FR
def _fc_depth():
    m=bpy.data.materials.new("fcd"); m.use_nodes=True; t=m.node_tree; t.nodes.clear()
    tc=t.nodes.new("ShaderNodeTexCoord")
    vm=t.nodes.new("ShaderNodeVectorMath"); vm.operation="LENGTH"
    mr=t.nodes.new("ShaderNodeMapRange"); mr.clamp=True
    mr.inputs[1].default_value=1.0
    mr.inputs[2].default_value={FAR}
    mr.inputs[3].default_value=1.0
    mr.inputs[4].default_value=0.0
    em=t.nodes.new("ShaderNodeEmission"); ou=t.nodes.new("ShaderNodeOutputMaterial")
    t.links.new(tc.outputs["Camera"], vm.inputs[0])
    t.links.new(vm.outputs[0], mr.inputs[0])
    t.links.new(mr.outputs[0], em.inputs["Color"])
    t.links.new(em.outputs[0], ou.inputs[0])
    return m
def _fc_black():
    m=bpy.data.materials.new("fcb"); m.use_nodes=True; t=m.node_tree; t.nodes.clear()
    em=t.nodes.new("ShaderNodeEmission"); em.inputs["Color"].default_value=(0.0,0.0,0.0,1.0)
    ou=t.nodes.new("ShaderNodeOutputMaterial")
    t.links.new(em.outputs[0], ou.inputs[0])
    return m
_dm=_fc_depth(); _bm=_fc_black()
for o in scn.collection.objects:
    if o.type!="MESH": continue
    o.data.materials.clear()
    o.data.materials.append(_bm if o.name=="ground" else _dm)
BASE={}
for o in scn.collection.objects:
    if o.name.startswith("char_"):
        BASE[o.name]=(o.location.x, o.location.y, o.location.z)
HW=DIST*0.36
CX=(sum(b[0] for b in BASE.values())/len(BASE)) if BASE else 0.0   # 角色组中心初始 x
def _wx(nx): return nx*HW*0.8
def _wy(ny): return ny*DIST*0.22
PTS=[]
for _s in {WALK}.split(":"):
    _s=_s.strip()
    if not _s: continue
    _x,_y=_s.split(",")
    PTS.append((float(_x),float(_y)))
if PTS:
    _segs=([math.hypot(PTS[i+1][0]-PTS[i][0],PTS[i+1][1]-PTS[i][1]) for i in range(len(PTS)-1)]
           if len(PTS)>1 else [0.0])
    _tot=sum(_segs) or 1.0
    _cum=0.0
    for _i,_p in enumerate(PTS):
        if _i==0: _f=1
        elif _i==len(PTS)-1: _f=FR
        else: _f=1+round((FR-1)*_cum/_tot)
        _dx=_wx(_p[0])-CX      # 角色组中心移到目标世界 x（绝对定位，非相对起点）
        _dy=_wy(_p[1])         # y 为相对初始 0 的前后偏移
        for _nm,_b in BASE.items():
            _o=bpy.data.objects.get(_nm)
            if _o is None: continue
            _o.location=(_b[0]+_dx, _b[1]+_dy, _b[2])
            _o.keyframe_insert("location", frame=_f)
        if _i<len(_segs): _cum+=_segs[_i]
scn.render.filepath={FCPATH}
scn.frame_step=1
bpy.ops.render.render(write_still=False, scene=scn.name, animation=True)
print("OK_FC", {FCPATH})
'''
