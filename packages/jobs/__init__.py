"""Job 统一内核的共享纯函数层。

P1 统一（docs/design/job-unification/PLAN_JOB_UNIFICATION.md）把 cron 求值
从 scheduler 插件上移到本包：``packages.core.background_jobs``（统一循环）与
``packages.scheduler``（兼容层）都依赖这里，避免 core 反向 import 插件。

cron 共享模块保持零 I/O。独立后台命令宿主位于 ``independent_service``，
其落盘与执行不依赖 Pan 生命周期；``pan_bridge`` 是显式启用的接线层。
旧 Job 内核仍在 ``packages.core.background_jobs``，不会自动迁移旧记录。
"""
