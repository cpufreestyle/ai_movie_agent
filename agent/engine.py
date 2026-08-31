"""视频引擎：封装 SkyReels-V2 的 Diffusion Forcing 推理脚本。

核心思路（持续创作 / 无限时长）：
- 首镜：以 --prompt 做文生视频(T2V)。
- 后续每一镜：传入 --video_path 指向上一版影片，SkyReels 会在其末尾
  续写出新片段（Diffusion Forcing 的 extend_video），从而实现无缝的
  "持续创作"。
- 角色一致：当提供 --image（D 阶段 ComfyUI 生成的关键帧）时，DF 脚本会
  以该图作为起始帧做 I2V，保证每镜开头画面与关键帧一致。

通过子进程调用官方 generate_video_df.py，规避内部 API 变动。
输出统一在仓库内 result/<outdir>/*.mp4，取最新一个移到目标路径。
"""
from __future__ import annotations

import glob
import os
import random
import shutil
import subprocess
import sys

from .llmutil import log


class SkyReelsEngine:
    def __init__(self, config: dict, agent_root: str):
        eng = config.get("engine", {})
        self.repo = os.path.abspath(os.path.join(agent_root, eng.get("skyreels_repo", "./skyreels_v2")))
        self.model_id = eng.get("model_id", "Skywork/SkyReels-V2-DF-1.3B-540P")
        self.resolution = eng.get("resolution", "540P")
        self.offload = bool(eng.get("offload", True))
        self.fps = int(eng.get("fps", 24))
        self.scene_frames = int(eng.get("scene_frames", eng.get("num_frames", 97)))
        self.ar_step = int(eng.get("ar_step", 0))
        self.overlap_history = int(eng.get("overlap_history", 17))
        self.addnoise_condition = int(eng.get("addnoise_condition", 20))
        self.base_num_frames = int(eng.get("base_num_frames", 97))
        self.guidance_scale = float(eng.get("guidance_scale", 6.0))
        self.inference_steps = int(eng.get("inference_steps", 30))
        self.python = eng.get("python") or self._find_python(agent_root)
        # I2V 二次续写：先用关键帧做 I2V 生成起始片段，再对该片段跑一轮 DF 续写/精修
        self.two_pass = bool(eng.get("two_pass_i2v", True))

    def _find_python(self, agent_root: str) -> str:
        for base in (agent_root, self.repo):
            cand = os.path.join(base, ".venv", "bin", "python")
            if os.path.exists(cand):
                return cand
        return sys.executable

    def is_ready(self) -> bool:
        return os.path.exists(os.path.join(self.repo, "generate_video_df.py"))

    def generate(
        self,
        prompt: str,
        out_path: str,
        prev_clip: str | None = None,
        seed: int | None = None,
        image: str | None = None,
        two_pass: bool | None = None,
    ) -> str:
        if not self.is_ready():
            raise RuntimeError(
                f"未找到 SkyReels 推理脚本：{self.repo}/generate_video_df.py\n"
                "请先运行 setup_wsl.sh 克隆 SkyReels-V2 仓库。"
            )
        if two_pass is None:
            two_pass = self.two_pass
        # 二次续写：有起始关键帧时，先 I2V 出起始片段，再 DF 续写/精修
        if two_pass and image and os.path.exists(image):
            tmp1 = out_path + ".i2v_pass1.mp4"
            # 第一遍：以关键帧为起始帧做 I2V（保留与上一镜的续写关系）
            self._run(prompt, tmp1, prev_clip=prev_clip, seed=seed, image=image)
            # 第二遍：把第一遍结果作为历史，再跑一轮 DF 续写/精修
            self._run(prompt, out_path, prev_clip=tmp1, seed=seed, image=None)
            try:
                os.remove(tmp1)
            except OSError:
                pass
            return out_path
        self._run(prompt, out_path, prev_clip=prev_clip, seed=seed, image=image)
        return out_path

    def _run(
        self,
        prompt: str,
        out_path: str,
        prev_clip: str | None = None,
        seed: int | None = None,
        image: str | None = None,
    ) -> str:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        outdir = f"agent_run_{random.randint(0, 1 << 30)}"
        cmd = [
            self.python, "generate_video_df.py",
            "--model_id", self.model_id,
            "--resolution", self.resolution,
            "--prompt", prompt,
            "--outdir", outdir,
            "--fps", str(self.fps),
            "--num_frames", str(self.scene_frames),
            "--ar_step", str(self.ar_step),
            "--overlap_history", str(self.overlap_history),
            "--addnoise_condition", str(self.addnoise_condition),
            "--base_num_frames", str(self.base_num_frames),
            "--guidance_scale", str(self.guidance_scale),
            "--inference_steps", str(self.inference_steps),
        ]
        if self.offload:
            cmd.append("--offload")
        if seed is not None:
            cmd += ["--seed", str(seed)]
        if prev_clip and os.path.exists(prev_clip):
            cmd += ["--video_path", prev_clip]
        if image and os.path.exists(image):
            cmd += ["--image", image]

        log(f"  [engine] 运行 SkyReels: {os.path.basename(out_path)} "
              f"({'I2V起始帧' if image else '续写' if prev_clip else '开新片'})")
        subprocess.run(cmd, cwd=self.repo, check=True)

        result_dir = os.path.join(self.repo, "result", outdir)
        mp4s = glob.glob(os.path.join(result_dir, "*.mp4"))
        if not mp4s:
            raise RuntimeError(f"SkyReels 未产出 mp4，目录: {result_dir}")
        src = max(mp4s, key=os.path.getmtime)
        shutil.move(src, out_path)
        shutil.rmtree(result_dir, ignore_errors=True)
        return out_path
