"""Task 运行时领域。

管理长时间运行的 async task（PDF parse、embed、extract、discover）。
状态机：Queued → Running → WaitingForUser | Succeeded | Failed | Cancelled。
"""
