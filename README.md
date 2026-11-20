# 胶片数字修复工作底稿

主子方向为数仓与数据湖。胶片档案馆从不同拷贝补齐同一部作品时，需要知道成片中每一段画面从何而来、哪些原始帧仍没有找到。仓库接收逐卷扫描记录、人工看片标记、翻拍映射和数字修复导出清单，形成可追查到原始素材的成片构成和质量底稿。最小基线可用两卷低分辨率扫描样例及一版成片清单跑通。

这是可运行的最小数据基线，只实现下表列明的能力。样例只处理帧位、标记和清单，不读取胶片扫描图像。

| 数据链 | 基线能力 | 离线样例 | 可核验结果 |
|---|---|---|---|
| `scan-coverage` | 对已知方向和原片起点的单段扫描列出对应帧及内部缺口。 | [输入](examples/scan-coverage.json) | [结果](tests/expected/scan-coverage.json) |
| `cut-order` | 为单版已确认镜头顺序计算片上帧位和各镜头的出现序号。 | [输入](examples/cut-order.json) | [结果](tests/expected/cut-order.json) |
| `damage-review` | 把同一帧的已复检损伤与仅初检存疑的帧分别列出。 | [输入](examples/damage-review.json) | [结果](tests/expected/damage-review.json) |
| `crop-review` | 以操作员标注的矩形画面核对导出裁切的有效画面损失和片边保留。 | [输入](examples/crop-review.json) | [结果](tests/expected/crop-review.json) |
| `render-lineage` | 按导出清单中的明确引用追查逐段原扫描、补绘或替代素材；扩展记录可对任意交付帧追到卷/批与实际扫描帧、停格延长、补绘底帧或占位，并区分两版间的换料、片位挪动与未建立对应。 | [输入](examples/render-lineage.json) | [结果](tests/expected/render-lineage.json) |

## 运行

在仓库根目录使用 Python 3.11 及以上版本，无需安装运行依赖：

```sh
python3 -m film_restoration_evidence scan-coverage examples/scan-coverage.json
python3 -m film_restoration_evidence scan-coverage examples/scan-coverage.json --sqlite facts.sqlite
python3 -m unittest discover -s tests -v
```

每个输入文件包含 `records` 数组，记录的 `id` 在批内唯一。各数据链校验自己的业务字段，输出按 `fact_id` 标识的事实。起止范围若无另行说明均为左闭右开；日期、深度、金额和持续时间等单位由字段标明。

加上 `--sqlite` 时，`runs` 表保存规范化的来源 JSON 与 SHA-256，`facts` 表保存同一批次的事实。原样重放不新增事实；写入失败回滚整批。既有数据库不会被自动改写为不同版本的处理结果。JSON 接口和入库只是数据处理的运行入口，不是仓库的业务主体。

## 成片来源复核（render-lineage 扩展记录）

同一条 `render-lineage` 批次可以同时放两类记录，互不影响：

- 只有 `sources`/`spans` 的旧版记录，仍按 span 给出 `scan`/`paint`/`placeholder`/`unresolved` 清单。
- 带 `versions`、`scans`、`materials`、`spans`、`correspondences` 的扩展记录，输出三类事实：
  - `segments`：把每个版本的交付时间轴切成同质来源段。扫描段按帧标记 `scan_found` 或 `scan_missing`；倒扫（`direction: reverse`）按 `source_origin` 与扫描长度换算原片帧号，漏扫帧即使被交付清单引用也只会得到 `scan_missing`，不算找到原扫描。引用扫描覆盖范围之外的帧直接判输入错误。
  - 停格段 `freeze_extended` 记录被延长的那一帧及其扫描状态；补绘段 `paint_delivered` 同时给出底帧、底帧扫描状态与 `base_frame_verified`——补绘可以交付画面而底帧仍待核，两件事分别可查；`placeholder` 表示占位画面。
  - `comparison`：按修复师确认的 `correspondences` 比较两版，分入 `source_changed`（来源身份确实变化）、`position_only`（仅因剪短/放长挪动片位，重叠序位来源一致）和 `unmatched`（未能建立对应的镜头出现）。同一镜头两次出现可各自引用不同卷/批的拷贝；来源身份只看卷、批、帧和素材性质，不看素材声明的内部编号。

全卷连续段不逐帧列入清单，但可对任意交付帧复核，且与查整段结论一致：

```python
from film_restoration_evidence import process
from film_restoration_evidence.render_lineage import lookup_frame, lookup_range

facts = process("render-lineage", source)
lookup_frame(facts, "render-lineage-02", "screening-cut", 11)
lookup_range(facts, "render-lineage-02", "archive-cut", 9, 12)
```

起止范围均为左闭右开；`lookup_range` 返回覆盖该范围的同质段，`uniform` 为真时范围内任意一帧的 `lookup_frame` 结论都相同。
