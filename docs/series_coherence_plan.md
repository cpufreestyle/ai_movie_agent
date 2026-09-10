# 三集剧情连贯性重做方案

> 目标：让《看见未来之前》三集构成**连续故事**——统一角色、统一视觉基调、清晰的因果时间线、跨集互文的解说词。
> 当前三集是从不同来源/模型**各自独立生成**再拼起来的，没有任何跨集的"单一事实来源"，故剧情必然断层。本方案分四层根治，并给出可尽早验证的执行路线。

---

## 1. 现状诊断（为什么现在不连贯）

| 维度 | 现状 | 后果 |
|---|---|---|
| 画面来源 | 第1集=B站下载中文版 `ep1_bili.mp4`；第2集=LTX-2.3 生成 `ltx23_film.mp4`；第3集=LTX-2.5 生成 `ltx25_film.mp4` | 每次独立 T2V + 独立随机 seed → Mira 长相/服装/城市/光线全不一致 |
| 台词 | `lines_seefuture.json` / `ep1_lines.json` / `lines_mira.json` 三份**独立手写**，格式 `{narration:[9], narration_en:[9]}` | 无跨集呼应，且**时间线错位**（见下） |
| 大纲 | `state.json` 的 `bible` 只有整部 logline + 10 段总 outline，**无分集级串联、无跨集钩子** | 生成时无"上一集接什么、本集留什么钩子"的约束 |
| 生成方式 | `cli ltx` 纯 T2V，第2/3集**未接上一集尾帧** | 画面从零开始，无视觉延续 |

**当前三集时间线错位**（按剧情逻辑重排）：
- `ep1_lines.json`（"第一次走进这座城…"）→ 应是**最早**：Mira 刚进城、兜里只剩一段童年。
- `lines_seefuture.json`（"雨还没停…记忆论克卖…门又亮了我记得自己是谁"）→ 身份觉醒的**诗化中段**。
- `lines_mira.json`（"我坐在工作台前…潜入前主人数据库…扳倒他"）→ 应是**最晚**：已掌控局面、对抗前主人。

→ 这三段被分别标成"第一/二/三集"，但叙事顺序并不连续，是根因之一。

---

## 2. 改进四层

### A. 剧情连贯（根因，最快见效，不依赖重跑模型）

**产出两份文件：**

1. `outputs/series_bible.json` —— 系列圣经（单一事实来源）
   ```json
   {
     "title": "《看见未来之前》",
     "world": "近未来霓虹赛博都市；记忆可被量化、买卖、植入",
     "characters": {
       "mira": {
         "role": "AI 重编程师",
         "look": "…（固定外貌/年龄/发型/常服描述，供 B 层视觉锚定）",
         "arc": ["初入城市的迷茫", "记忆交易中的身份动摇", "潜入前主人数据库、直面真相"]
       }
     },
     "timeline": "明确三集分别处于 Mira 人生的哪个阶段（待用户拍板排序）",
     "episodes": [
       { "ep": 1, "hook_in": "（序章/接上集无）",
         "beats": ["…本集发生…"], "hook_out": "留给第2集的钩子" },
       { "ep": 2, "hook_in": "承接第1集结尾…", "beats": ["…"], "hook_out": "留给第3集的钩子" },
       { "ep": 3, "hook_in": "承接第2集结尾…", "beats": ["…"], "hook_out": "（开放结局或收束）" }
     ]
   }
   ```

2. `outputs/series_script.json` —— 连贯分集剧本（由 bible 派生）
   - 结构：`{ "ep1": {narration:[9], narration_en:[9]}, "ep2": {...}, "ep3": {...} }`
   - 要求：**跨集互文**——第2集台词点名第1集事件，第3集回应第2集；每集开头接上集 `hook_out`，结尾留 `hook_out` 给下集。
   - 生成：用现有 `agent.planner`（Ollama `gemma4:e2b`，非推理模型）基于 `series_bible.json` 产出；或 LLM 直出后人工定稿。

**改动点：**
- `make_narration.py` 增加 `--series-script outputs/series_script.json --ep <N>`，从统一剧本切出指定集台词（替代现在散落的 `lines_*.json`）。
- 旧三份 `lines_*.json` 仅作参考，不再作为成片台词来源。

### B. 角色 / 视觉一致

- **Mira 角色参考图（character sheet）**：用 ComfyUI 出一张定妆照（或复用 Blender 白模渲染），固定其作为每集 I2V 起始帧/参考，保证长相服装统一。
- **风格锚 prompt 模板**：每集 prompt 末尾固定拼接
  `anime style, cel-shaded, clean line art, vibrant colors, dramatic lighting, slow camera movement, <固定城市描述>, <Mira 固定服装描述>`
  写入 `config.yaml` 的 `engine.comfyui_ltx.style_anchor`，引擎注入时追加。
- **固定 seed 策略**：`seed_ep = BASE_SEED + ep * 1000`，三集共享基调、集间可辨。

### C. 画面尾帧续写（I2V 链，技术已就绪）

- `LTXEngine.generate(prompt, out, image=prev_last_frame)` 已完整支持 I2V（`_inject` 里 `load_image` + `i2v_enable=True` 分支）；`cli ltx --image <帧>` 已暴露。
- **新增 `run_series.py`** 串联：
  1. 第1集 T2V 出片 → `ffmpeg` 抽尾帧 `outputs/series/ep1_last.png`
  2. 第2集 `LTXEngine.generate(prompt2, ep2.mp4, image=ep1_last.png)`（I2V 续写）
  3. 抽 `ep2_last.png` → 第3集 I2V。
- **关键约束**：当前第1集 `ep1_bili.mp4` 是**下载的烧字幕中文版**，无法做干净的 I2V 续写源头 → **第1集画面需用 LTX 重新出片**（见路线图 Phase 3），才能串起整条视觉链。

### D. 配音 / 字幕互文

- 已在 A 的 `series_script.json` 里落实（跨集呼应台词）。`make_narration.py` 无需大改，只换输入源。
- 维持现有默认：**英文配音 + 中英双语字幕**（用户 09-06 规则）。

---

## 3. 执行路线图（分阶段，可尽早验证）

| Phase | 内容 | 是否需 GPU | 验证点 |
|---|---|---|---|
| **0** | 写 `series_bible.json` + 重排三集时间线 | 否 | 你确认时间线排序与角色设定 |
| **1** | 基于 bible 生成 `series_script.json`（连贯三集剧本） | 否 | 剧本读起来是连续故事 |
| **2** | `make_narration.py` 改读 `series_script.json`；重出三集**配音版**（不重跑模型） | 否 | 听三集解说，确认剧情连贯 |
| **3** | Mira 角色参考图 + 风格锚 prompt + 固定 seed（B 层） | 出参考图需 | 三集人物/基调一致 |
| **4** | `run_series.py` 尾帧续写重出三集画面（C 层，含重出第1集） | 是（ComfyUI+GPU） | 画面角色/场景延续 |
| **5** | 新画面 + 新剧本 → `make_narration` 合成 → 上传三集 | 合成需 ffmpeg | 线上三集为连贯成片 |

Phase 0–2 当天即可完成并验证"剧情连贯"，无需动 GPU；Phase 3–5 再处理视觉与重出。

---

## 4. 待你确认的关键决策

1. **三集时间线排序**：采用上文重排（进城→觉醒→对抗前主人），还是你另有设定？
2. **第1集画面是否重出**：放弃下载的 `ep1_bili.mp4`（烧字幕），改用 LTX 重出以串起 I2V 视觉链？
3. **角色参考图来源**：ComfyUI 出定妆照，还是 Blender 白模渲染？
4. **集数范围**：先重做这三集，还是预留更多集的扩展结构？

> 确认后从 Phase 0 开始落地。所有产物落 `outputs/`，剧本/圣经不进 `.git` 噪声（按需）。
