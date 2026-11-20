# 胶片数字修复工作底稿

主子方向为数仓与数据湖。胶片档案馆从不同拷贝补齐同一部作品时，需要知道成片中每一段画面从何而来、哪些原始帧仍没有找到。仓库接收逐卷扫描记录、人工看片标记、翻拍映射和数字修复导出清单，形成可追查到原始素材的成片构成和质量底稿。最小基线可用两卷低分辨率扫描样例及一版成片清单跑通。

这是可运行的最小数据基线，只实现下表列明的能力。样例只处理帧位、标记和清单，不读取胶片扫描图像。

| 数据链 | 基线能力 | 离线样例 | 可核验结果 |
|---|---|---|---|
| `scan-coverage` | 对已知方向和原片起点的单段扫描列出对应帧及内部缺口。 | [输入](examples/scan-coverage.json) | [结果](tests/expected/scan-coverage.json) |
| `cut-order` | 为单版已确认镜头顺序计算片上帧位和各镜头的出现序号。 | [输入](examples/cut-order.json) | [结果](tests/expected/cut-order.json) |
| `damage-review` | 把同一帧的已复检损伤与仅初检存疑的帧分别列出。 | [输入](examples/damage-review.json) | [结果](tests/expected/damage-review.json) |
| `crop-review` | 以操作员标注的矩形画面核对导出裁切的有效画面损失和片边保留；扩展记录在设备取景范围、画幅比例与最小尺寸内选定全片固定的放映框，保住各段必留区域、避开已确认损伤，并使已看片范围内保留的有效画面最多。 | [输入](examples/crop-review.json) | [结果](tests/expected/crop-review.json) |
| `render-lineage` | 按导出清单中的明确引用追查逐段原扫描、补绘或替代素材。 | [输入](examples/render-lineage.json) | [结果](tests/expected/render-lineage.json) |

## 运行

在仓库根目录使用 Python 3.11 及以上版本，无需安装运行依赖：

```sh
python3 -m film_restoration_evidence scan-coverage examples/scan-coverage.json
python3 -m film_restoration_evidence scan-coverage examples/scan-coverage.json --sqlite facts.sqlite
python3 -m unittest discover -s tests -v
```

每个输入文件包含 `records` 数组，记录的 `id` 在批内唯一。各数据链校验自己的业务字段，输出按 `fact_id` 标识的事实。起止范围若无另行说明均为左闭右开；日期、深度、金额和持续时间等单位由字段标明。

加上 `--sqlite` 时，`runs` 表保存规范化的来源 JSON 与 SHA-256，`facts` 表保存同一批次的事实。原样重放不新增事实；写入失败回滚整批。既有数据库不会被自动改写为不同版本的处理结果。JSON 接口和入库只是数据处理的运行入口，不是仓库的业务主体。

## 放映框评估（crop-review 扩展记录）

同一条 `crop-review` 批次可以同时放两类记录，互不影响：

- 只有 `picture`/`export` 的旧版记录，仍输出 `lost_picture_px` 与 `retained_border_px`。
- 带 `device`、`frame_count`、`views` 的扩展记录，按以下口径输出一条事实：

```json
{
  "id": "crop-review-02",
  "frame_count": 240,
  "device": {"viewport": [0, 0, 2048, 1556], "aspect": [4, 3], "min_size": [1280, 960]},
  "views": [
    {"start": 0, "end": 96,
     "picture": [40, 24, 1980, 1500],
     "keep": [[300, 1180, 1750, 1340]],
     "damage": [{"rect": [1980, 60, 2040, 1500], "state": "confirmed"}]}
  ]
}
```

- 所有 `picture`、`keep`、`damage`、`viewport` 矩形都在同一套看片坐标下，`[x0, y0, x1, y1]`；帧段 `[start, end)` 左闭右开。
- `device.viewport` 是取景范围；`aspect` 可为数字或 `[宽, 高]`；`min_size` 或 `min_width`+`min_height` 是最小画面尺寸。放映框必须落在取景范围内、符合画幅比例且不小于最小尺寸。
- 每条 `view` 覆盖一段帧并给出该段画面边界、必留区域和损伤标记。损伤 `state` 取 `suspected`（待复检）、`confirmed`（已确认）或 `excluded`（复检排除）。标记也可带 `frames: [start, end)`，表示只覆盖该记录中的一部分帧。
- 同帧被多条记录重复看片时，画面取并集，不因重复记录增加权重。
- 放映框全片固定：必须包含每一段的必留区域，且不得与任何已确认损伤重叠。若两者无法兼顾，`frame` 为 `null`，`conflicts` 给出冲突的帧段与区域（`keep_damage`），复检记录自相矛盾（同一区域既确认又排除）时给出 `review_conflict`，必留区域超出取景范围时给出 `keep_outside_viewport`，设备条件本身无解时给出 `device`。
- 可行框不止一个时，选已看片范围内保留画面最多者；面积按各段覆盖的帧数累加，同一帧的重叠画面只算一次。同分取较小的框。
- 待复检的划伤既不算已确认损伤，也不算干净：不缩小放映框去藏它，但会进入 `pending_review` 并阻止 `finalized`。未看片范围列入 `unviewed_ranges`，同样阻止定案。
- `segments` 按帧段给出结论（`decided`、`pending_review`、`conflict`、`unviewed`），相邻同结论同面积的段自动合并，长片不必逐帧重复。任意帧或整段可用 `lookup_frame`、`lookup_range` 复核，结论与 `segments` 一致。

```python
from film_restoration_evidence import process
from film_restoration_evidence.crop_review import lookup_frame, lookup_range

facts = process("crop-review", source)
lookup_frame(facts, "crop-review-02", 120)
lookup_range(facts, "crop-review-02", 90, 180)
```
