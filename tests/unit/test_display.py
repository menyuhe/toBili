from tobili.desktop.display import fitted_bounds


def test_remote_resolution_shrink_keeps_titlebar_and_resize_edges_reachable():
    assert fitted_bounds((1500, 900, 2070, 1380), (0, 0, 1366, 768)) == (24, 24, 1342, 744)


def test_normal_window_size_and_position_unchanged():
    assert fitted_bounds((50, 50, 1200, 800), (0, 0, 2560, 1400)) == (50, 50, 1200, 800)


def test_negative_monitor_origin_and_small_work_area():
    x, y, w, h = fitted_bounds((-3000, -500, 2000, 1400), (-1280, 0, 1280, 720))
    assert x >= -1280 and y >= 0 and x + w <= 0 and y + h <= 720
