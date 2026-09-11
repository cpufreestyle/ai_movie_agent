"""白模（blocking）生成：通过 Blender MCP 渲染灰度预览 / 控制图 / 灰模动画，辅助 AI 视频制作。

四种用途（已确认全选）：
 1) 分镜 previs 预览图：在 concept_video 分镜卡里用白模图展示构图/机位/站位；
 2) AI 视频起始帧/参考：白模图作为 I2V 起始帧，锁定角色站位与机位；
 3) ControlNet 条件图：由白模渲染 depth / normal / line 作控制图（供 ComfyUI 注入）；
 4) Blender 直出灰模动画：相机运镜的灰模帧序列，混入成片或作 blocking 动画。

前置：本地已安装 Blender + Blender MCP 插件，并在 Blender 内启动 MCP Server（端口 9876）。
未就绪时 is_ready() 返回 False，调用方降级跳过，不影响现有管线。

本次优化（性能 → 健壮性 → 质量）：
- 性能：渲染引擎可配（auto 优先 EEVEE(GPU)，失败降级 CYCLES）；previs+depth 合并为一次
  渲染（省一次整场景渲染）；同一集连续同几何分镜复用已建场景（只更新相机）；采样数可配。
- 健壮性：生成的 bpy 代码先做语法预检；执行后校验 4 张产物确实存在且非空；失败按
  「合并+快引擎 → 合并+CYCLES → 逐张+CYCLES」逐级降级重试（次数可配）；仍失败则抛
  BlockingError，杜绝"返回不存在的图"这种静默失败。
- 质量：depth 用固定近远平面归一化（动画多帧一致，不再逐帧 Normalize 抖动）；
  每次渲染前复位白模材质（修复 normal 步骤换材质后污染后续渲染的隐患）。
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


class BlockingError(RuntimeError):
    """白模渲染失败（已重试仍无有效产物）。"""


class BlockingGenerator:
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
        self.anim_frames = int(self.cfg.get("anim_frames", 24))

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
        """
        n = max(1, int(spec.get("characters", 1)))
        props = [str(p) for p in (spec.get("props", []) or [])]
        key = json.dumps({"c": n, "p": props}, sort_keys=True, ensure_ascii=False)
        return int(hashlib.md5(key.encode("utf-8")).hexdigest()[:8], 16)

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
        # eevee / eevee_next / auto：优先 EEVEE(GPU)，不可用再退 CYCLES
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
        """渲染降级链：合并+快引擎 → 合并+CYCLES → 逐张+CYCLES。"""
        fast = self.engine if self.engine != "auto" else "eevee"
        plans = [("merged", fast), ("merged", "cycles"), ("legacy", "cycles")]
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
        c = self._common(spec, out_dir)
        c["ENGINE"] = self._engine_setup(engine)
        core = self._fill(_CORE_TEMPLATE, **c)
        cam = self._fill(_CAM_TEMPLATE, **c)
        tail = self._fill(_TAIL_MERGED if mode == "merged" else _TAIL_LEGACY, **c)
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
        for idx, (mode, engine) in enumerate(plans):
            code = self._build_block_code(spec, out_dir, mode, engine)
            self._check_syntax(code)
            self.client.timeout = self.timeout
            ex = self.client.exec_code_ex(code)
            if not ex["ok"]:
                last_err = ex["error"] or "Blender 执行失败"
                log(f"  [blocking] 白模渲染第 {idx+1} 次失败（{mode}/{engine}）: {last_err[:200]}")
                continue
            miss = self._missing(paths)
            if not miss:
                if idx > 0:
                    log(f"  [blocking] 白模渲染在第 {idx+1} 次尝试成功（{mode}/{engine}）")
                return paths
            last_err = f"产物缺失: {', '.join(miss)}"
            log(f"  [blocking] 白模渲染第 {idx+1} 次产物不完整（{mode}/{engine}）: {last_err}")
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
        ff = shutil.which("ffmpeg")
        if not ff:
            log("  [blocking] 未找到 ffmpeg，跳过灰模动画合成（ref_video 不可用）")
            return None
        fps = int(((self.config.get("engine", {}) or {}).get("fps", 24)) or 24)
        try:
            subprocess.run([ff, "-y", "-loglevel", "error", "-framerate", str(fps),
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
# 场景几何（地面/角色/道具）。带签名复用：几何不变则只更新相机，不重建场景。
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
else:
    try:
        bpy.context.window.scene=scn
    except Exception:
        pass
scn["blk_sig"]=SIG
# 复位白模材质：normal 步骤会把材质换成法线材质，必须每次渲染前恢复，
# 否则复用场景时 previs 会渲染成法线图（原实现的隐患）。
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

# 相机（每镜都更新；场景复用时不重建物体也能改机位）
_CAM_TEMPLATE = r'''
DIST={"wide":9.0,"medium":5.5,"close":3.2}.get({SHOT},5.5)
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

# 首选：previs+depth 合并为一次渲染（不同通道），line / normal 各一次 → 共 3 次
_TAIL_MERGED = r'''
W={W}; H={H}
scn.render.resolution_x=W; scn.render.resolution_y=H; scn.render.resolution_percentage=100
scn.render.image_settings.file_format="PNG"
{ENGINE}
scn.use_nodes=False
scn.render.use_freestyle=False
# --- A) previs + depth：一次渲染、两个输出槽（省掉一次整场景渲染）---
scn.use_nodes=True
nt=scn.node_tree
nt.nodes.clear()
rl=nt.nodes.new("CompositorNodeRLayers")
fo=nt.nodes.new("CompositorNodeOutputFile")
fo.base_path={OUTDIR} + "/"
try:
    fo.format.file_format="PNG"
except Exception:
    pass
try:
    fo.file_slots[0].path="previs_"
    nt.links.new(rl.outputs["Image"], fo.inputs[0])
except Exception:
    pass
mr=nt.nodes.new("CompositorNodeMapRange")
try:
    mr.inputs[1].default_value={NEAR}
    mr.inputs[2].default_value={FAR}
    mr.inputs[3].default_value=0.0
    mr.inputs[4].default_value=1.0
    nt.links.new(rl.outputs["Depth"], mr.inputs[0])
    fo.file_slots.new("depth_")
    nt.links.new(mr.outputs[0], fo.inputs[-1])
except Exception:
    pass
scn.render.filepath={PREVIS}
bpy.ops.render.render(write_still=True, scene=scn)
# File Output 产物自带帧号（如 previs_0001.png），改名回固定名
import glob as _glob
def _grab(pref, dst):
    c=sorted(_glob.glob(os.path.join({OUTDIR}, pref+"*.png")))
    if c:
        try:
            os.replace(c[-1], dst)
        except Exception:
            pass
_grab("previs_", {PREVIS})
_grab("depth_", {DEPTH})
# --- B) line：freestyle 线框（强制 CYCLES，保证 freestyle 生效）---
scn.use_nodes=False
try:
    scn.render.engine="CYCLES"
    scn.cycles.samples={LS}
except Exception:
    pass
scn.render.use_freestyle=True
scn.render.filepath={LINE}
bpy.ops.render.render(write_still=True, scene=scn)
# --- C) normal：材质法线可视化（渲染后由下次 core 复位材质）---
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
scn.render.use_freestyle=False
scn.render.filepath={NORMAL}
bpy.ops.render.render(write_still=True, scene=scn)
print("OK_BLOCK", {PREVIS}, {LINE}, {DEPTH}, {NORMAL})
'''

# 降级：最保守的四张分别渲染（与历史已验证路径等价），仅 depth 改用固定范围
_TAIL_LEGACY = r'''
W={W}; H={H}
scn.render.resolution_x=W; scn.render.resolution_y=H; scn.render.resolution_percentage=100
scn.render.image_settings.file_format="PNG"
{ENGINE}
# 1) previs 白模（无 freestyle）
scn.render.use_freestyle=False; scn.use_nodes=False
scn.render.filepath={PREVIS}
bpy.ops.render.render(write_still=True, scene=scn)
# 2) line 线框（freestyle）
scn.render.use_freestyle=True
scn.render.filepath={LINE}
bpy.ops.render.render(write_still=True, scene=scn)
# 3) depth 深度（固定近远平面归一化，保证动画多帧一致）
scn.use_nodes=True; nt=scn.node_tree; nt.nodes.clear()
rl=nt.nodes.new("CompositorNodeRLayers")
mr=nt.nodes.new("CompositorNodeMapRange")
mr.inputs[1].default_value={NEAR}; mr.inputs[2].default_value={FAR}
mr.inputs[3].default_value=0.0; mr.inputs[4].default_value=1.0
co=nt.nodes.new("CompositorNodeComposite")
nt.links.new(rl.outputs["Depth"], mr.inputs[0]); nt.links.new(mr.outputs[0], co.inputs[0])
scn.render.use_freestyle=False
scn.render.filepath={DEPTH}
bpy.ops.render.render(write_still=True, scene=scn)
# 4) normal 法线预览（材质法线 -> 颜色）
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
scn.use_nodes=False
scn.render.filepath={NORMAL}
bpy.ops.render.render(write_still=True, scene=scn)
print("OK_BLOCK", {PREVIS}, {LINE}, {DEPTH}, {NORMAL})
'''

# 灰模动画（相机运镜帧序列）
_ANIM_TAIL = r'''
W={W}; H={H}
scn.render.resolution_x=W; scn.render.resolution_y=H; scn.render.resolution_percentage=100
scn.render.image_settings.file_format="PNG"
{ENGINE}
scn.use_nodes=False
scn.render.use_freestyle=False
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
bpy.ops.render.render(write_still=False, scene=scn, animation=True)
print("OK_ANIM", {OUTDIR})
'''
