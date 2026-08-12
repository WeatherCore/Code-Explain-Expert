# tests/ — 测试与样例

本目录包含 skill 的冒烟测试与样例产物，**不是** skill 运行时依赖。最终交付给用户的 skill 不含本目录也能正常工作；本目录仅用于开发期回归保障与样例对照。

## 目录结构

```
tests/
├── test_smoke.py              # 冒烟测试（纯标准库，不依赖 pytest）
├── README.md                  # 本文件
├── fixtures/                  # 被测样本项目（最小可运行工程）
│   ├── sample-java/           #   Spring Boot 支付编排示例（pom.xml + 3 源文件 + 1 测试）
│   └── sample-py/             #   Python 订单/支付示例（requirements.txt + 2 源文件 + 1 测试）
└── out/
    └── ZHIDAO.example.md      # 脚本生成的 ZHIDAO.md 样例（payment-demo）
```

## 各文件用途

### `test_smoke.py`
三个脚本（`extract_skeleton` / `fetch_sources` / `bigfile_split`）的冒烟测试。验证：
1. 脚本能跑通、退出码 0
2. 输出 JSON 合法、字段符合 `SKILL.md` 约定（`total_files` / `skipped_test_files` / `language_hint` / `chunk_file=None` 等）
3. **客户项目零污染**：跑完脚本后 `fixtures/` 目录文件快照无变化（无新增 `.json` / `.txt` / `.bak`）
4. `.work/` 默认落盘 + 清理生命周期正常

跑法：
```bash
python tests/test_smoke.py
```

### `fixtures/`
两个最小可运行工程，用于冒烟测试与手动验证脚本行为：
- `sample-java/`：3 个源文件 + 1 个测试（验证 Java 正则解析 + 测试文件跳过）
- `sample-py/`：2 个源文件 + 1 个测试（验证 Python ast 解析 + 测试文件跳过）

故意做得极小，只覆盖"能识别类/方法/import/依赖边"这一层，不覆盖复杂业务。

### `out/ZHIDAO.example.md`
脚本对 `fixtures/sample-java` 实跑生成的 `ZHIDAO.md` 样例，用于验证脚本输出形态符合 `references/navigation-guide.md` 的 10 章黄金模板。

**与 `references/samples/ZHIDAO.md` 的定位区别**（避免维护者困惑）：

| 文件 | 定位 | 用途 |
|---|---|---|
| `references/samples/ZHIDAO.md` | **人工认可的黄金样例**（open_deep_research 项目） | LLM 生成 ZHIDAO.md 时对照风格 |
| `tests/out/ZHIDAO.example.md` | **脚本生成的样例**（payment-demo 项目） | 验证脚本输出形态符合模板 |

两者指向不同项目、用途不同，不是重复。

## 维护约定

- 改动 `scripts/` 后必须跑 `python tests/test_smoke.py` 确认 25 项全过
- `fixtures/` 是只读样本，不要往里加业务代码（保持极小）
- `out/ZHIDAO.example.md` 可在 `navigation-guide.md` 大改后重新生成替换
