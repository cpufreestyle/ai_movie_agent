"""白模（blocking）生成：通过 Blender MCP 渲染灰度预览 / 控制图 / 灰模动画，辅助 AI 视频制作。

四种用途（已确认全选）：
  1) 分镜 previs 预览图：在 concept_video 分镜卡里用白模图展示构图/机位/站位；
  2) AI 视频起始帧/参考：白模图作为 SkyReels I2V 起始帧，锁定角色站位与机位；
  3) ControlNet 条件图：由白模渲染 depth / normal / line 作控制图（供 ComfyUI 注入）；
  4) Blender 直出灰模动画：相机运镜的灰模帧序列，混入成片或作 blocking 动画。

前置：本地已安装 Blender + Blender MCP 插件，并在 Blender 内启动 MCP Server（端口 9876）。
未就绪时 is_ready() 返回 False，调用方降级跳过，不影响现有管线。
"""
from __future__ import annotations

import os
import sys
import json

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.blender_mcp import BlenderMCP
from agent.llmutil import make_client, chat, extract_json


class BlockingGenerator:
    def __init__(self, config: dict, workdir: str):
        self.config = config
        self.cfg = config.get("blender", {}) or {}
        self.enabled = bool(self.cfg.get("enabled", False))
        self.workdir = os.path.abspath(workdir)
        self.host = self.cfg.get("host", "127.0.0.1")
        self.port = int(self.cfg.get("port", 9876))
        self.client = BlenderMCP(self.host, self.port)
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
                print(f"  [blocking] LLM 解析失败，用规则兜底: {e}")
        return spec

    # ---------- 代码模板填充 ----------
    @staticmethod
    def _fill(code: str, **kw) -> str:
        for k, v in kw.items():
            code = code.replace("{" + k + "}", str(v))
        return code

    def _core(self, spec: dict) -> str:
        n = max(1, int(spec.get("characters", 1)))
        return self._fill(_CORE_TEMPLATE,
                          N=str(n),
                          PROPS=json.dumps(spec.get("props", []), ensure_ascii=False),
                          SHOT=json.dumps(spec.get("shot", "medium")),
                          HEIGHT=json.dumps(spec.get("height", "eye")),
                          CAMERA=json.dumps(spec.get("camera", "static")))

    def _block_tail(self, spec: dict, out_dir: str) -> str:
        return self._fill(_BLOCK_TAIL,
                          W=str(self.width), H=str(self.height),
                          PREVIS=json.dumps(os.path.join(out_dir, "previs.png")),
                          LINE=json.dumps(os.path.join(out_dir, "line.png")),
                          DEPTH=json.dumps(os.path.join(out_dir, "depth.png")),
                          NORMAL=json.dumps(os.path.join(out_dir, "normal.png")))

    def _anim_tail(self, spec: dict, out_dir: str, frames: int) -> str:
        return self._fill(_ANIM_TAIL,
                          W=str(self.width), H=str(self.height),
                          OUTDIR=json.dumps(out_dir), FRAMES=str(frames))

    # ---------- 渲染 ----------
    def render_block(self, spec: dict, out_dir: str) -> dict:
        """一次出 4 张图：previs 白模 / line 线框 / depth 深度 / normal 法线。"""
        os.makedirs(out_dir, exist_ok=True)
        code = self._core(spec) + "\n" + self._block_tail(spec, out_dir)
        self.client.exec_code(code)
        return {
            "previs": os.path.join(out_dir, "previs.png"),
            "line": os.path.join(out_dir, "line.png"),
            "depth": os.path.join(out_dir, "depth.png"),
            "normal": os.path.join(out_dir, "normal.png"),
        }

    def render_previs(self, spec: dict, out_path: str) -> str:
        out_dir = os.path.dirname(os.path.abspath(out_path))
        os.makedirs(out_dir, exist_ok=True)
        return self.render_block(spec, out_dir)["previs"]

    def render_control(self, spec: dict, out_dir: str) -> dict:
        return self.render_block(spec, out_dir)

    def render_animation(self, spec: dict, out_dir: str, frames: int | None = None) -> str:
        frames = frames or self.anim_frames
        os.makedirs(out_dir, exist_ok=True)
        code = self._core(spec) + "\n" + self._anim_tail(spec, out_dir, frames)
        self.client.exec_code(code)
        return out_dir

    def render_assets(self, prompts: list[str]) -> dict:
        """批量生成全套白模资产（每镜）。返回 {previews, controls, anims}。"""
        res = {"previews": [], "controls": [], "anims": []}
        for i, p in enumerate(prompts):
            spec = self.parse_spec(p)
            d = os.path.join(self.out_dir, f"shot_{i:03d}")
            blk = self.render_block(spec, d)
            res["previews"].append(blk["previs"])
            res["controls"].append(blk)
            if spec.get("camera") != "static":
                anim_dir = os.path.join(d, "anim")
                res["anims"].append(self.render_animation(spec, anim_dir, self.anim_frames))
            else:
                res["anims"].append(None)
        return res


# ---------- Blender (bpy) 代码模板 ----------
_CORE_TEMPLATE = r'''
import bpy, os, math
SCN="blocking_tmp"
if SCN in bpy.data.scenes:
    bpy.data.scenes.remove(bpy.data.scenes[SCN])
scn=bpy.data.scenes.new(SCN)
bpy.context.window.scene=scn
for o in list(scn.collection.objects):
    bpy.data.objects.remove(o, do_unlink=True)
scn.world=bpy.data.worlds.get("World") or bpy.data.worlds.new("World")

def wm(obj, shade):
    m=bpy.data.materials.new("wm"); m.use_nodes=False; m.diffuse_color=(shade,shade,shade,1.0)
    if obj.data.materials: obj.data.materials[0]=m
    else: obj.data.materials.append(m)

bpy.ops.mesh.primitive_plane_add(size=14)
g=bpy.context.object; g.name="ground"; wm(g,0.55)

N={N}; props={PROPS}
xs=[ (i-(N-1)/2.0)*2.4 for i in range(N) ]
for i,x in enumerate(xs):
    bpy.ops.mesh.primitive_cylinder_add(radius=0.35, depth=1.4, location=(x,0,0.7))
    b=bpy.context.object; b.name=("char_%d"%i); wm(b,0.85)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.3, location=(x,0,1.5))
    h=bpy.context.object; h.name=("char_%d_h"%i); wm(h,0.9)
for p in props:
    bpy.ops.mesh.primitive_cube_add(size=0.8, location=(2.8,-1.2,0.4))
    o=bpy.context.object; o.name=("prop_"+p); wm(o,0.7)

DIST={"wide":9.0,"medium":5.5,"close":3.2}.get({SHOT},5.5)
CAMY=-DIST
CAMZ={"eye":1.6,"low":0.6,"high":4.0}.get({HEIGHT},1.6)
bpy.ops.object.camera_add(location=(0,CAMY,CAMZ))
cam=bpy.context.object; cam.name="block_cam"
bpy.ops.object.empty_add(location=(0,0,1.0)); tgt=bpy.context.object; tgt.name="cam_tgt"
cam.constraints.clear()
ct=cam.constraints.new("TRACK_TO"); ct.target=tgt; ct.track_axis="TRACK_NEGATIVE_Z"; ct.up_axis="UP_Y"
scn.camera=cam
'''

_BLOCK_TAIL = r'''
W={W}; H={H}
try:
    scn.render.engine="BLENDER_EEVEE_NEXT"
except Exception:
    scn.render.engine="BLENDER_EEVEE"
scn.render.resolution_x=W; scn.render.resolution_y=H
scn.render.image_settings.file_format="PNG"
# 1) previs 白模（无 freestyle）
scn.render.use_freestyle=False; scn.use_nodes=False
scn.render.filepath={PREVIS}
bpy.ops.render.render(write_still=True, scene=scn)
# 2) line 线框（freestyle）
scn.render.use_freestyle=True
scn.render.filepath={LINE}
bpy.ops.render.render(write_still=True, scene=scn)
# 3) depth 深度（compositor 归一化 Z）
scn.use_nodes=True; nt=scn.node_tree; nt.nodes.clear()
rl=nt.nodes.new("CompositorNodeRLayers"); nz=nt.nodes.new("CompositorNodeNormalize"); co=nt.nodes.new("CompositorNodeComposite")
nt.links.new(rl.outputs["Depth"], nz.inputs[0]); nt.links.new(nz.outputs[0], co.inputs[0])
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

_ANIM_TAIL = r'''
W={W}; H={H}
try:
    scn.render.engine="BLENDER_EEVEE_NEXT"
except Exception:
    scn.render.engine="BLENDER_EEVEE"
scn.render.resolution_x=W; scn.render.resolution_y=H
scn.render.image_settings.file_format="PNG"
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
