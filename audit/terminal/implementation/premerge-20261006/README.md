# 合入前证据索引（2026-10-06）

候选生产基线：`d34dd978aa5e61290755d8690e1d0db87e695575`。
main/practical 对照：`362d47ff0cd926275dab96b1b54aff7b256ef109`。
详细结论与部署前置见 `docs/design/PAN_TERMINAL_PREMERGE_20261006.md`。

## 结果分列

| 报告 | 事实 |
| --- | --- |
| python-full.xml | 首轮 2782 passed / 3 failed / 10 skipped；三失败是自然退出旧夹具 |
| python-final.xml | 2797 passed / 1 failed / 5 skipped；组合旧 total 与稍后 snapshot 比较竞态 |
| composition-final-job.xml | 修正后整个组合文件 15 passed；不等价于第三次全量通过 |
| composition-targeted-job.xml | 修正前另一 UTF-8/OSC 组合用例 1 passed，不覆盖全量失败用例 |
| natural-compat-fixed.xml | 自然退出、归档及合成旧数据 39 passed |
| legacy-data.xml | 旧数据 8 passed，合成临时数据；已被最终全量覆盖，不重复计数 |
| previously-skipped.xml | 43 passed / 3 errors；旧探针导出缺失，保留初始错误 |
| previously-skipped-fixed.xml | 修正探针与 tzdata 后 46 passed |
| job-retention.xml | 真实 reparse 负控等 42 passed |
| optional-api.xml | 26 passed；已在全量执行，不算独立新增覆盖 |
| plugin-suites.xml | QQ/WeChat 166 passed；不在显式 tests/ 全量中 |
| frontend-full.json | 首轮 1317 passed / 3 failed |
| frontend-main-baseline.json | main 同三个文件 79 passed / 相同 3 failed |
| frontend-full-fixed.json | 1319 passed / 1 个 SessionList 5 秒超时 |
| frontend-timeout-targeted.json | 对应文件 16 passed |
| frontend-final.json | 最终 1320 passed |
| lint-candidate.json | 85 errors / 19 warnings |
| lint-main-baseline.json | 85 errors / 18 warnings；新增 error signature 0 |

浏览器证据使用兄弟目录 `browser/premerge-20261006`、
`browser/premerge-natural-20261006`、`browser/premerge-archive-20261006`。
历史证据不覆盖，构建产物为忽略的本地 dist，不作为源码提交。

## Job 与源码

直接执行隔离 pytest 的 Job：`job_91948a3320856c0ffe2e9e0a`，exit 0。
采用旧服务允许的 `D:/project/Pan` cwd，argv 使用候选树绝对 wrapper；
wrapper 清继承 PAN_* 并切换到自身仓库。投递后 MA idle，通知后读 XML。

全量等待 Job 的 exit 0 因 PowerShell XML 解析错误不能证明测试通过；
以原 pytest exit 1 和 XML 为准，未修改原报告。

修正后测试源码 Git blob（LF 规范化内容，不是 CRLF 工作树 SHA256）：

- tests/test_terminal_composition.py: `3e97198ebf98e8ebe092eeaac8d6466423c3b81d`
- tests/test_premerge_legacy_data.py: `9a65f25fcfd75463e461d1c44a06a53d871549a7`
- tests/test_terminal_natural_exit_ma.py: `2d30a99e88d5b1fd6a183a87fd5d36eafb3fd18e`

本轮生产源码不改，真实用户数据不读写迁移，main/practical 不推进，服务不重启。
源码与文档 diff-check 无告警；完整 staged diff-check 的 11 处尾随空白来自
pytest 原始失败 XML 的 traceback / 输出缩进，保留报告原文，不宣称全 diff-check 通过。
