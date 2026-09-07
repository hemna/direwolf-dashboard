from pathlib import Path


APP_JS = (
    Path(__file__).parents[1]
    / "src"
    / "direwolf_dashboard"
    / "static"
    / "app.js"
)


def test_resize_observer_is_the_single_distance_ring_resize_path():
    source = APP_JS.read_text()

    assert "map.on('resize', updateDistanceRings);" not in source
    assert "map.on('zoomend moveend', updateDistanceRings);" in source

    observer_start = source.index("new ResizeObserver(function () {")
    observer_end = source.index("}).observe(mapContainer);", observer_start)
    observer = source[observer_start:observer_end]

    assert "map.invalidateSize({ pan: false });" in observer
    assert "updateDistanceRings();" in observer
