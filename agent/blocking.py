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
  （只更新相机）；采样数可配。
- 健壮性：生成的 bpy 代码先做语法预检；执行后校验 4 张产物确实存在且非空；失败按
  「配置引擎 → CYCLES」降级重试；仍失败抛 BlockingError，杜绝"返回不存在的图"这种静默失败。
- 质量：补上场景光源（原实现无灯，白模只有 world 环境光 -> 整体偏暗、缺立体感）；
  freestyle 线框显式开 View Layer 开关并确保 LineSet 存在（否则 line.png 与 previs 完全相同）；
  depth 用「材质法」(CameraData.View Z Depth + MapRange 固定近远平面) 而非 compositor，
  跨 Blender 版本可用且动画多帧一致；每次渲染前复位白模材质，修复 depth/normal 换材质后
  污染后续渲染的隐患；相机距离随角色数补偿，避免角色贴边。

已适配的 Blender 5.x 变更（真机实测踩到）：
- `scene.node_tree`(compositor) 已移除（改用 compositing_node_group）-> 不用 compositor 出图；
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
        mmh3 = ((self.config.get("engine", {}) or {}).get("comfyui_mmH3", {}) or {})
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
        """
        if _ffmpeg_exe:
            p = _ffmpeg_exe()
            if p:
                return p
        p = shutil.which("ffmpeg")
        if p:
            return p
        try:
            import imageio_ffmpeg  # type: ignore
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:          # noqa: BLE001 - 没装则视为不可用
            return None

    # ---------- 就绪 ----------
    def is_ready(self) -> bool:
        if not self.enabled:
            return False
        return self.client.is_ready()

    # ---------- 分镜文本 -> 场景 spec ----------
    def parse_spec(self, text: str) -> dict:
        spec = {"characters": 1, "props": [], "shot": "medium",
                "camera": "static", "height": "eye"}
        t = text or ""
        if any(k in t for k in ("全景", "远景", "wide", "establishing", "大远景")):
            spec["shot"] = "wide"
        elif any(k in t for k in ("特写", "近景", "close", "cu", "大特写")):
            spec["shot"] = "close"
        if any(k in t for k in ("摇", "pan")):
            spec["camera"] = "pan"
        elif any(k in t for k in ("推", "dolly", "推进", "拉")):
            spec["camera"] = "dolly"
        elif any(k in t for k in ("移", "track", "横移", "跟拍")):
            spec["camera"] = "track"
        elif any(k in t for k in ("环绕", "orbit", "旋转", "转圈")):
            spec["camera"] = "orbit"
        if any(k in t for k in ("低机位", "仰拍", "low")):
            spec["height"] = "low"
        elif any(k in t for k in ("高机位", "俯拍", "航拍", "high", "俯视")):
            spec["height"] = "high"
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
        """
        fast = self.engine if self.engine != "auto" else "eevee"
        plans = [("block", fast), ("block", "cycles")]
        uniq = []
        for p in plans:
            if p not in uniq:
                uniq.append(p)
        return uniq[: max(1, int(self.retries) + 1)]

    def _common(self, spec: dict, out_dir: str) -> dict:
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
        )

    def _build_block_code(self, spec: dict, out_dir: str, mode: str, engine: str) -> str:
        """mode 参数保留以兼容旧调用（block 渲染路径已统一，不再区分 merged/legacy）。"""
        c = self._common(spec, out_dir)
        c["ENGINE"] = self._engine_setup(engine)
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
        for p in files:
            try:
                if os.path.exists(p):
                    os.remove(p)
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

    def render_assets(self, prompts: list) -> dict:
        """批量生成全套白模资产（每镜）。返回 {previews, controls, anims}。

        - previews: 白模 previs 图路径（None 表示该镜失败）
        - controls: {previs,line,depth,normal} 控制图路径字典（None 表示失败）
        - anims:    灰模运镜 mp4 路径（static 机位或合成失败为 None）

        单镜失败不影响其它镜（该项记 None 并记录日志），避免一次渲染问题拖垮整批。
        """
        res = {"previews": [], "controls": [], "anims": []}
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
# 2) line 线框：freestyle 仅 CYCLES / legacy EEVEE 支持，强制 CYCLES 保证线条生效
try:
    scn.render.engine="CYCLES"
    scn.cycles.samples={LS}
    scn.cycles.use_denoising=True
except Exception:
    pass
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
