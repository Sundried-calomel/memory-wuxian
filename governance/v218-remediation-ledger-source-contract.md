# v2.18 模块整改台账来源合同

- 写作模式：`new_composition`
- 审查模式：`source_grounded`
- 基线提交：`72998fd4e051816f57ac2c56d94579e0db45f8b1`
- V1 提交：`c4ae78dabae8c0f2618288d6e6a5957af07cf6a7`
- V2/当前提交：`6c514ccbdd075a5f45cf1d956471177edf7555ee`

## 允许来源

- 上述三个 Git 提交中的源代码和文件行数。
- `docs/module-architecture.json` 与当前源代码中的函数位置。
- `governance/v218-architecture-gate-validation.json`。
- `governance/v218-lean-dedup-v2-validation.json`。
- 用户已经确认的 V0-V4 分阶段范围。

## 允许操作

- 复算物理行数、定位当前函数、归类已完成和待完成工作。
- 对未来改动给出策略、风险、验证门槛和明确标注的区间估算。

## 禁止操作

- 把未来估算写成已测结果。
- 把行数下降作为唯一成功标准。
- 宣称尚未执行的 V3/V4 改造或测试已经完成。
- 在本任务中修改生产代码、测试、归档、摘要、云同步或安装状态。
