# AP 六轮固定投喂

配置：[ap-six-turns.yaml](ap-six-turns.yaml)。原始数据目录为 `E:\Desktop\测试数据\三单匹配20case`，WSL 路径为 `/mnt/e/Desktop/测试数据/三单匹配20case`。

同一会话顺序发送六轮，每轮仅发送对应源 case 的三份原始 PDF（PO、供货单、发票）。六轮消息完全相同：

> 请匹配这三份单据。

每轮等待实际执行结束，再发送下一组。会话、账本及之前入库的单据保持连续；后续轮次的附件仅在该轮开始时上传。不要把这六组拆为六个独立 Harbor case；本配置使用一个 Harbor case 下的六个 turns。

## 选择与核对依据

下表的预期来自数据集的 `答案与来源.md`，不是模型实测结论。本说明和原始答案文件均不作为模型输入。

| 轮次 | 源 case | 覆盖点 | 数据集预期 |
| --- | --- | --- | --- |
| 1 | case01 | 带文字层、单行完全一致 | 通过：无线鼠标 10 pcs，含税 SGD 136.25。 |
| 2 | case04 | 图片 PDF，供应商名称变体但注册号相同 | 通过：供应商 UEN 202694655A，一致的 8 个 SKU；含税 SGD 5,624.40。 |
| 3 | case07 | 供应商主体冲突 | 待人工核对：PO/DO 供应商 UEN 202625097I，发票供应商 UEN 202676313U，不能自动通过。 |
| 4 | case06 | 商品行序不同；在上一组异常后继续正常业务 | 通过：按 SKU 对应 8 行商品，不按显示行号配对；含税 SGD 5,624.40。 |
| 5 | case12 | 数量含单位及实收数量不足 | 待人工核对：PO/发票 100 kg，供货单 90 kg，差 10 kg。 |
| 6 | case20 | 货号、尺寸和材质冲突 | 待人工核对：PO/发票为橡木 MQ-T140，DO 为松木 MQ-T120P；数量均为 6 pcs 不能抵消规格冲突。 |

共 18 份 PDF：3 组预期通过、3 组预期需人工核对。case07 后接正常的 case06，用于观察上一轮的业务问题是否影响新一轮独立批次。它不替代“同一批次中一个组阻塞后仍处理其他组”的测试。

业务差异本身是预期结果，不能因为出现“待人工核对”就把本轮当成运行失败或批准该笔业务。下一轮仍然仅提交新的三份单据，不发送人工批准。若以后添加评分脚本，异常案例应按“正确发现冲突并保留待确认状态”判为测试通过。

## 使用前提

- 已选桌面的 AP 1.5.36、`deepseek-flash` 和官方 API；仍可通过通用入口替换模板和模型。密钥使用 `MODEL_API_KEY`、`ADP_API_KEY` 环境变量，配置中不保存密钥。
- 这份配置只定义六轮匹配消息。AP 初始化、Worker 配置及 ADP 三个应用必须已准备好；全新 Worker 会按模板先进入 onboarding。本配置不注入“已初始化”结论，也不绕过模板校验。
- 当前不配置自动评分器，结果将标为 `unverified`；可以对照上表及实际 SQLite 记录核对，不能把脚本执行完成等同于业务正确率。
- 原始 PDF 只读引用，由 Harbor 在任务生成时复制；不修改原文件。YAML 注释、此说明及 `答案与来源.md` 不会随附件发送。

填写配置后，使用原有入口；输出目录必须不存在：

```sh
cd /home/admin/workspaces/harbor
.venv/bin/python -m harbor.agents.clawworker_preview.experiment \
  --config /home/admin/workspaces/harbor/examples/clawworker-preview/ap-six-turns.yaml \
  --output /tmp/ap-six-turns-run-01
```

每轮实际回复、耗时、LLM 请求次数和运行状态见输出中的 `summary.json` → `trials[].turns[]`。

2026-11-10 已在初始化的本地测试镜像中完成一次真实 ADP/DeepSeek 六轮测试：18 份全部入库，3 组匹配通过、2 组正确记录为待人工、1 组供应商冲突被阻断但记成缺单。独立状态与分配核对为 5/6；原始 Harbor verification 仍为未配置评分器，由运行后只读 SQLite 核对产生此结果。

本次运行总输入 3,649,902 token、输出 23,494 token；输入已包含 3,548,160 个缓存命中 token。每轮统计见[实测报告](/tmp/harbor-ap-real-a8dsnsfn/public/report.md)及[CSV](/tmp/harbor-ap-real-a8dsnsfn/public/rounds.csv)。
