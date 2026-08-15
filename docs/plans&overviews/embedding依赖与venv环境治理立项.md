# Embedding 依赖与 .venv 环境治理 — 立项

> 基于 2026-08-13 工作树 .venv 差异排查发现的环境一致性问题，立项记录考量与待决策项。
> 状态：立项阶段（仅记录考量，**不删代码、不改依赖**） | 创建：2026-08-13

---

## 一、立项背景

排查各工作树 `.venv` 差异时发现环境严重不一致：

| 工作树 | 分支 | venv 包数 | 状态 |
|--------|------|-----------|------|
| `/project/Pan` | main | 175 | 超集：requirements + 手动装的 ML 全家桶 |
| `/project/pan-test` | pan-test | 110 | requirements 较新版本，但**缺 ML 链** |
| `/project/frontend-react` | frontend/react | 2 | 空 venv（只有 pip） |
| `/project/frontend-vanilla` | frontend/vanilla | 无 | 无 venv |

Python 均为 3.14.5（C 盘 Python314），**requirements.txt 在各分支间无差异**（`git diff` 为空）。差异根源不在分支，而在"有没有手动装过 ML 依赖链"。

**核心风险**：pan-test / 前端工作树没有 ML 依赖，而记忆系统默认走 sentence-transformers provider —— 一旦代码路径执行到 embedding，就会运行时 `ImportError`。环境不可复现，且失败是运行时才暴露的。

---

## 二、事实调查结论

### 2.1 记忆系统的默认 provider 是 sentence-transformers

`character.py:261`、`server.py:1446` 创建 `MemoryManager` 时显式指定：

```python
provider=PROVIDER_SENTENCE_TRANSFORMERS
```

`memory_context.py:122` 的默认值同样是 `PROVIDER_SENTENCE_TRANSFORMERS`。全代码库**没有任何调用点**使用 `openai`/`ollama`/`local` provider。

### 2.2 main venv 多出的 ML 包 = sentence-transformers 依赖链

`pip show sentence-transformers` 确认其硬依赖：

```
Requires: huggingface-hub, numpy, scikit-learn, scipy, torch, tqdm, transformers, typing_extensions
```

main venv 里多出的包（torch / transformers / scikit-learn / scipy / numpy / huggingface_hub / safetensors / tokenizers 等）**全是这条依赖链的传递依赖**，删除会直接破坏记忆系统。**结论：有用，不能删。**

### 2.3 llama-cpp-python 是"预留未启用"的依赖

- 加入时间：commit `176cdcf`（feat(memory): Phase 1，2026-07-29）
- 用途：embedder.py 设计的 4 个 provider 之一 `PROVIDER_LOCAL`（llama.cpp GGUF 本地 embedding）的运行时依赖
- 现状：local 分支代码完整实现（`embedder.py:120, 440, 476` 等），但**从未被任何调用点激活**

### 2.4 sentence-transformers 与 llama.cpp 都是本地推理，差异在运行时

| | sentence-transformers | llama.cpp (`local`) |
|---|---|---|
| 推理位置 | 本机 CPU/GPU | 本机 CPU/GPU |
| 模型格式 | HF transformer（`BAAI/bge-base-zh-v1.5`） | GGUF 量化（`embeddinggemma-300m-Q8_0.gguf`） |
| 运行时 | **PyTorch**（torch + transformers 全家桶，数个 GB） | **llama.cpp**（C++，轻量，几百 MB） |
| 首次使用 | 从 HF 下载模型一次，之后离线 | 从 HF 下载 GGUF 一次，之后离线 |

两者都满足"离线本地"诉求。差异在资源开销与依赖体积：sentence-transformers 背 PyTorch 全家桶（这是 main venv 175 包的根源），llama.cpp 不需要 torch，更轻量。

---

## 三、核心考量问题

### Q1: requirements.txt 不完整，环境不可复现

`requirements.txt` 没有 `sentence-transformers`（及其依赖链），导致：
- 按 requirements 装出来的环境跑不了记忆系统（运行时才 ImportError）
- 各工作树 .venv 因此分化

**候选方案**：
- **A（推荐）**：`requirements.txt` 加入 `sentence-transformers`，使环境可复现
- B：main venv 用 `pip freeze` 固化一个 `requirements-full.txt`，与精简版并存
- C：维持现状（不推荐——分化只会越来越严重）

### Q2: llama-cpp-python 去留

- 保留：embedder.py 已支持 `PROVIDER_LOCAL`，未来切离线轻量方案只需接上调用点
- 删除：如果确定只走 sentence-transformers，可去掉这行；local 分支代码不删也不会报错（未被调用）

### Q3: 默认 provider 要不要切到 local（llama.cpp）

用户已明确"先不删"。但值得立项评估：

| 维度 | sentence-transformers（现状） | llama.cpp local |
|------|------------------------------|-----------------|
| 依赖体积 | torch 全家桶（数 GB） | 轻量 |
| 模型下载 | 需现拉 bge-base-zh-v1.5 | 需现拉 GGUF（200-300MB） |
| 切换成本 | 现状 | 需修改 2 处调用点 + 首次下载模型 |
| 适用 | 模型丰富、生态成熟 | 资源受限、纯 CPU 场景 |

---

## 四、待决策事项

- [ ] **requirements.txt 是否补 `sentence-transformers`？** — 建议补（A 方案），保证环境可复现
- [ ] **llama-cpp-python 保留还是移除？** — 倾向保留（代码已实现 local 分支，仅需后续接调用点）
- [ ] **默认 provider 是否切换？** — 倾向维持 sentence-transformers；llama.cpp local 作为"离线轻量备选"记录在案
- [ ] **各工作树环境如何对齐？** — 建议 main 作为基准，pan-test 补齐依赖后验证记忆系统
- [ ] **前端工作树是否需建 Python venv？** — 倾向不需要（纯前端分支，无 Python 依赖）

---

## 五、关联记录

- `packages/core/memory/embedder.py` — 4 provider 定义与实现
- `packages/core/character.py:258` — `get_memory_manager`（ST provider）
- `packages/web/server.py:1423` — `_get_memory_manager`（ST provider）
- `packages/core/memory_context.py:110-130` — 默认 ST provider
- commit `176cdcf` — 记忆系统 Phase 1，引入 llama-cpp-python
