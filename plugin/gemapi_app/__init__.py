def classFactory(iface):
    from .perf import measure
    with measure("plugin.import_dependencies"):
        from .plugin import SigMilieuxUX
    return SigMilieuxUX(iface)
