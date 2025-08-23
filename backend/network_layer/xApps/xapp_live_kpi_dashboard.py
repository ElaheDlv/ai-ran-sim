# xapp_live_kpi_dashboard.py
# Live KPI dashboard (no CSV) that mirrors the per‑UE/Cell KPIs your collector writes.
# Drop this file into: network_layer/xApps/
#
# Requires:
#   pip install dash==2.* plotly==5.*
#
# Starts a small Dash server on http://localhost:8061 and plots:
#   - Per‑UE: DL bitrate (Mbps), SINR (dB), CQI, DL buffer (bytes)*
#   - Per‑UE: Allocated DL PRBs (if available from cell.prb_ue_allocation_dict)
#   - Per‑Cell: DL load (0–1), Allocated vs Max DL PRBs
# Fields marked * are optional and shown if present on UE objects.
#
# Notes:
# - We read KPIs directly from self.ue_list / self.cell_list each sim step.
# - We use getattr(...) everywhere so it won't crash if some fields are not present.
# - To keep memory small we store a rolling window (MAX_POINTS).

from .xapp_base import xAppBase

import threading
from collections import defaultdict, deque

# Dash / Plotly
# pip install dash==2.* plotly==5.*
from dash import Dash, dcc, html, Input, Output, State
import plotly.graph_objs as go

MAX_POINTS = 600      # ~ last 5 minutes at 0.5 s refresh
REFRESH_SEC = 0.5
DASH_PORT = 8061

def _deque():
    return deque(maxlen=MAX_POINTS)

class xAppLiveKPIDashboard(xAppBase):
    def __init__(self, ric=None):
        super().__init__(ric=ric)
        self.enabled = True

        # Rolling time axis (simulation step)
        self._t = _deque()

        # --- Per‑UE series ---
        self._ue_dl_mbps = defaultdict(_deque)      # {IMSI: deque}
        self._ue_sinr_db = defaultdict(_deque)      # {IMSI: deque}
        self._ue_cqi     = defaultdict(_deque)      # {IMSI: deque}
        self._ue_dl_buf  = defaultdict(_deque)      # optional: {IMSI: deque}
        self._ue_dl_prb  = defaultdict(_deque)      # from cell allocation map if present

        # --- Per‑Cell series ---
        self._cell_dl_load    = defaultdict(_deque)  # {cell_id: deque in [0,1]}
        self._cell_alloc_prb  = defaultdict(_deque)  # {cell_id: deque}
        self._cell_max_prb    = defaultdict(_deque)  # {cell_id: deque} (constant but we plot to show headroom)

        # Concurrency
        self._lock = threading.Lock()

        # Dash server thread
        self._dash_app = None
        self._dash_thread = None

        # Last step seen (avoid double pushes)
        self._last_step = None

    # ---------------- xApp lifecycle ----------------

    def start(self):
        if not self.enabled:
            print(f"{self.xapp_id}: disabled")
            return
        self._start_dashboard()

    def step(self):
        """Collect KPIs each simulation step."""
        sim_step = getattr(getattr(self.ric, "simulation_engine", None), "sim_step", None)
        if sim_step is None or sim_step == self._last_step:
            return
        self._last_step = sim_step

        with self._lock:
            self._t.append(sim_step)

            # ---- Per‑UE ----
            for imsi, ue in self.ue_list.items():
                # DL bitrate (bps -> Mbps)
                dl_bps = float(getattr(ue, "downlink_bitrate", 0.0) or 0.0)
                self._ue_dl_mbps[imsi].append(dl_bps / 1e6)

                # SINR (dB)
                sinr = getattr(ue, "downlink_sinr", None)
                if sinr is not None:
                    self._ue_sinr_db[imsi].append(float(sinr))

                # CQI
                cqi = getattr(ue, "downlink_cqi", None)
                if cqi is not None:
                    self._ue_cqi[imsi].append(float(cqi))

                # Optional queues/buffers (if your UE defines them)
                if hasattr(ue, "dl_buffer_bytes"):
                    self._ue_dl_buf[imsi].append(float(getattr(ue, "dl_buffer_bytes", 0.0) or 0.0))

                # Allocated PRBs for this UE (DL), if cell exposes prb_ue_allocation_dict
                cell = getattr(ue, "current_cell", None)
                if cell is not None:
                    alloc_map = getattr(cell, "prb_ue_allocation_dict", {}) or {}
                    ue_key = imsi if imsi in alloc_map else getattr(ue, "ue_imsi", None)
                    ue_alloc = alloc_map.get(ue_key, {})
                    dl_prb = ue_alloc.get("downlink", None)
                    if dl_prb is not None:
                        self._ue_dl_prb[imsi].append(float(dl_prb))

            # ---- Per‑Cell ----
            for cell_id, cell in self.cell_list.items():
                load = getattr(cell, "current_dl_load", None)
                if load is not None:
                    self._cell_dl_load[cell_id].append(float(load))

                alloc_dl = getattr(cell, "allocated_dl_prb", None)
                if alloc_dl is not None:
                    self._cell_alloc_prb[cell_id].append(float(alloc_dl))

                max_prb = getattr(cell, "max_dl_prb", None)
                if max_prb is not None:
                    self._cell_max_prb[cell_id].append(float(max_prb))

    # ---------------- Dash server ----------------

    def _start_dashboard(self):
        if self._dash_thread and self._dash_thread.is_alive():
            return

        app = Dash(__name__)
        self._dash_app = app

        def ue_options():
            return [{"label": imsi, "value": imsi} for imsi in self.ue_list.keys()]
        def cell_options():
            return [{"label": cid, "value": cid} for cid in self.cell_list.keys()]

        app.layout = html.Div(
            style={"fontFamily": "system-ui, -apple-system, Segoe UI, Roboto, sans-serif", "padding": "12px"},
            children=[
                html.H2("Live RAN KPIs (no CSV)"),
                html.P("Streaming KPIs directly from UEs/Cells within the simulator."),

                html.Div(style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "12px"}, children=[
                    html.Div([
                        html.Label("Focus UEs (optional)"),
                        dcc.Dropdown(id="ue-filter", multi=True, options=ue_options()),
                    ]),
                    html.Div([
                        html.Label("Focus Cells (optional)"),
                        dcc.Dropdown(id="cell-filter", multi=True, options=cell_options()),
                    ]),
                ]),

                html.Hr(),

                html.Div(style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "12px"}, children=[
                    dcc.Graph(id="ue-bitrate"),
                    dcc.Graph(id="ue-sinr-cqi"),
                ]),

                html.Div(style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "12px", "marginTop": "12px"}, children=[
                    dcc.Graph(id="ue-prb"),
                    dcc.Graph(id="cell-load"),
                ]),

                html.Div(style={"display": "grid", "gridTemplateColumns": "1fr", "gap": "12px", "marginTop": "12px"}, children=[
                    dcc.Graph(id="ue-buffer"),
                ]),

                dcc.Interval(id="tick", interval=int(REFRESH_SEC * 1000), n_intervals=0),
            ]
        )

        @app.callback(
            Output("ue-bitrate", "figure"),
            Output("ue-sinr-cqi", "figure"),
            Output("ue-prb", "figure"),
            Output("cell-load", "figure"),
            Output("ue-buffer", "figure"),
            Input("tick", "n_intervals"),
            State("ue-filter", "value"),
            State("cell-filter", "value"),
        )
        def _update(_n, ue_filter, cell_filter):
            with self._lock:
                tx = list(self._t)
                if not tx:
                    # Empty figures before first sample
                    return go.Figure(), go.Figure(), go.Figure(), go.Figure(), go.Figure()

                if ue_filter is None or len(ue_filter) == 0:
                    ue_keys = list(set(
                        list(self._ue_dl_mbps.keys())
                        + list(self._ue_sinr_db.keys())
                        + list(self._ue_cqi.keys())
                        + list(self._ue_dl_buf.keys())
                        + list(self._ue_dl_prb.keys())
                    ))
                else:
                    ue_keys = ue_filter

                if cell_filter is None or len(cell_filter) == 0:
                    cell_keys = list(set(
                        list(self._cell_dl_load.keys())
                        + list(self._cell_alloc_prb.keys())
                        + list(self._cell_max_prb.keys())
                    ))
                else:
                    cell_keys = cell_filter

                # --- UE bitrate (Mbps) ---
                tr_bitrate = []
                for imsi in ue_keys:
                    ys = list(self._ue_dl_mbps.get(imsi, []))
                    if ys:
                        tr_bitrate.append(go.Scatter(x=tx[-len(ys):], y=ys, mode="lines", name=f"{imsi} DL Mbps"))
                fig_bitrate = go.Figure(
                    data=tr_bitrate,
                    layout=go.Layout(title="Per‑UE Downlink Bitrate (Mbps)",
                                     xaxis={"title": "Sim step"}, yaxis={"title": "Mbps"})
                )

                # --- UE SINR & CQI (two y-axes) ---
                tr_sinr = []
                tr_cqi = []
                for imsi in ue_keys:
                    ys_s = list(self._ue_sinr_db.get(imsi, []))
                    if ys_s:
                        tr_sinr.append(go.Scatter(x=tx[-len(ys_s):], y=ys_s, mode="lines", name=f"{imsi} SINR (dB)", yaxis="y1"))
                    ys_c = list(self._ue_cqi.get(imsi, []))
                    if ys_c:
                        tr_cqi.append(go.Scatter(x=tx[-len(ys_c):], y=ys_c, mode="lines", name=f"{imsi} CQI", yaxis="y2"))
                fig_sinr_cqi = go.Figure(
                    data=tr_sinr + tr_cqi,
                    layout=go.Layout(
                        title="Per‑UE SINR & CQI",
                        xaxis={"title": "Sim step"},
                        yaxis={"title": "SINR (dB)", "side": "left"},
                        yaxis2={"title": "CQI", "overlaying": "y", "side": "right", "range": [0, 15]},
                    )
                )

                # --- UE DL PRBs ---
                tr_prb = []
                for imsi in ue_keys:
                    ys = list(self._ue_dl_prb.get(imsi, []))
                    if ys:
                        tr_prb.append(go.Scatter(x=tx[-len(ys):], y=ys, mode="lines", name=f"{imsi} DL PRBs"))
                fig_prb = go.Figure(
                    data=tr_prb,
                    layout=go.Layout(title="Per‑UE Allocated DL PRBs", xaxis={"title": "Sim step"}, yaxis={"title": "PRBs"})
                )

                # --- Cell load & PRBs ---
                tr_cell = []
                for cid in cell_keys:
                    ys = list(self._cell_dl_load.get(cid, []))
                    if ys:
                        tr_cell.append(go.Scatter(x=tx[-len(ys):], y=ys, mode="lines", name=f"{cid} DL load"))
                for cid in cell_keys:
                    ys_a = list(self._cell_alloc_prb.get(cid, []))
                    if ys_a:
                        tr_cell.append(go.Scatter(x=tx[-len(ys_a):], y=ys_a, mode="lines", name=f"{cid} alloc PRB", line={"dash": "dot"}))
                    ys_m = list(self._cell_max_prb.get(cid, []))
                    if ys_m:
                        tr_cell.append(go.Scatter(x=tx[-len(ys_m):], y=ys_m, mode="lines", name=f"{cid} max PRB", line={"dash": "dash"}))
                fig_cell = go.Figure(
                    data=tr_cell,
                    layout=go.Layout(title="Per‑Cell Load & PRBs",
                                     xaxis={"title": "Sim step"}, yaxis={"title": "Value / PRBs"})
                )

                # --- UE DL buffer (optional) ---
                tr_buf = []
                for imsi in ue_keys:
                    ys = list(self._ue_dl_buf.get(imsi, []))
                    if ys:
                        tr_buf.append(go.Scatter(x=tx[-len(ys):], y=ys, mode="lines", name=f"{imsi} DL buffer (bytes)"))
                fig_buf = go.Figure(
                    data=tr_buf,
                    layout=go.Layout(title="Per‑UE DL Buffer (bytes)*",
                                     xaxis={"title": "Sim step"}, yaxis={"title": "Bytes"})
                )

            return fig_bitrate, fig_sinr_cqi, fig_prb, fig_cell, fig_buf

        def _run():
            app.run_server(host="127.0.0.1", port=DASH_PORT, debug=False)

        self._dash_thread = threading.Thread(target=_run, daemon=True)
        self._dash_thread.start()
        print(f"{self.xapp_id}: live KPI dashboard at http://localhost:{DASH_PORT}")
