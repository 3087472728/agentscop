from importlib.metadata import requires

deps = requires("agentscope")
for extra in ["service", "storage-redis", "storage-sql", "rag", "vdb-qdrant", "channel", "tools", "storage"]:
    names = [d.split(";")[0].strip() for d in deps if f'extra == "{extra}"' in d]
    print(extra, "->", names)
