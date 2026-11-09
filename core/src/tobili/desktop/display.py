"""Keep the Windows shell usable across monitor, DPI and remote-session changes."""
import logging


def fitted_bounds(bounds, area):
    x,y,w,h=bounds
    ax,ay,aw,ah=area
    w=min(w,max(1,aw-24));h=min(h,max(1,ah-24))
    return (max(ax,min(x,ax+aw-w)),max(ay,min(y,ay+ah-h)),w,h)


def attach(window, home):
    if getattr(window,'_display_attached',False):return
    window._display_attached=True
    from System import Action
    from System.Drawing import Size,Rectangle
    from System.Windows.Forms import Screen,FormWindowState
    from Microsoft.Win32 import SystemEvents
    import json
    form=window.native

    def fit(*_):
        if form.IsDisposed:return
        if form.InvokeRequired:
            form.BeginInvoke(Action(lambda:fit()))
            return
        area=Screen.FromControl(form).WorkingArea
        bounds=form.Bounds
        scale=float(form.DeviceDpi)/96
        form.MinimumSize=Size(min(int(800*scale),max(320,area.Width-24)),min(int(520*scale),max(240,area.Height-24)))
        if form.WindowState==FormWindowState.Normal:
            form.Bounds=Rectangle(*fitted_bounds((bounds.X,bounds.Y,bounds.Width,bounds.Height),(area.X,area.Y,area.Width,area.Height)))
        view=getattr(form,'webview',None)
        if view is not None and view.CoreWebView2 is not None:
            view.ZoomFactor=1.0
            view.CoreWebView2.Settings.IsZoomControlEnabled=False
        path=home/'logs/window-display.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({'dpi':form.DeviceDpi,'zoom':float(view.ZoomFactor) if view is not None else None,'client':[form.ClientSize.Width,form.ClientSize.Height],'working_area':[area.Width,area.Height]}))

    def safe_fit(*args):
        try:fit()
        except Exception:logging.exception('Window display adjustment failed')

    def setup():
        form.DpiChanged+=safe_fit
        form.Activated+=safe_fit
        SystemEvents.DisplaySettingsChanged+=safe_fit
        SystemEvents.SessionSwitch+=safe_fit
        def close(*_):
            SystemEvents.DisplaySettingsChanged-=safe_fit
            SystemEvents.SessionSwitch-=safe_fit
        form.FormClosed+=close
        safe_fit()
    form.Invoke(Action(setup))
