"""Job 统一内核的共享纯函数层。

P1 统一（docs/design/job-unification/PLAN_JOB_UNIFICATION.md）把 cron 求值
从 scheduler 插件上移到本包：``packages.core.background_jobs``（统一循环）与
``packages.scheduler``（兼容层）都依赖这里，避免 core 反向 import 插件。

本包只放零 I/O、零全局状态的纯函数模块；有状态/落盘的内核代码仍在
``packages.core.background_jobs``。
"""
