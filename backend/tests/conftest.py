import os

# 必须在导入 app.* 之前生效：测试用内存 SQLite，不依赖 Postgres。
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("SEED_ON_EMPTY", "false")
