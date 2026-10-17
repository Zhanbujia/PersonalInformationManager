"""总结 worker：只处理 `digest` 任务。

生成一次总结要调一次强档模型 + 多表统计，是重活。单独一个进程池的好处是
它可以独立限流——不会因为有人点"生成本月总结"就让记录入库排队。
"""

from __future__ import annotations

from workers.worker import main

if __name__ == "__main__":
    main(["digest"])
