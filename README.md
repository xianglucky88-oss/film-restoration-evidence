# 胶片数字修复工作底稿

主子方向为数仓与数据湖。胶片档案馆从不同拷贝补齐同一部作品时，需要知道成片中每一段画面从何而来、哪些原始帧仍没有找到。仓库接收逐卷扫描记录、人工看片标记、翻拍映射和数字修复导出清单，形成可追查到原始素材的成片构成和质量底稿。最小基线可用两卷低分辨率扫描样例及一版成片清单跑通。

这是可运行的最小数据基线，只实现下表列明的能力。样例只处理帧位、标记和清单，不读取胶片扫描图像。

| 数据链 | 基线能力 | 离线样例 | 可核验结果 |
|---|---|---|---|
| `scan-coverage` | 对已知方向和原片起点的单段扫描列出对应帧及内部缺口。 | [输入](examples/scan-coverage.json) | [结果](tests/expected/scan-coverage.json) |
| `cut-order` | 为单版已确认镜头顺序计算片上帧位和各镜头的出现序号。 | [输入](examples/cut-order.json) | [结果](tests/expected/cut-order.json) |
| `damage-review` | 把同一帧的已复检损伤与仅初检存疑的帧分别列出。 | [输入](examples/damage-review.json) | [结果](tests/expected/damage-review.json) |
| `crop-review` | 以操作员标注的矩形画面核对导出裁切的有效画面损失和片边保留。 | [输入](examples/crop-review.json) | [结果](tests/expected/crop-review.json) |
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
