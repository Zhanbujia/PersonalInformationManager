"""入库 worker：只处理 `ingest` 任务。

单独一个入口是为了能按需扩容：记录是高频路径，总结是低频重活，
把两类任务放在同一个进程池里，会让"偶尔生成一次月总结"拖慢"记一条"。
"""

from __future__ import annotations

from workers.worker import main

if __name__ == "__main__":
    main(["ingest"])
