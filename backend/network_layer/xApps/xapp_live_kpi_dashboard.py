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

from dash.exceptions import PreventUpdate


from settings import (
    RAN_PRB_CAP_SLIDER_DEFAULT, RAN_PRB_CAP_SLIDER_MAX
)

#MAX_POINTS = 600      # ~ last 5 minutes at 0.5 s refresh
MAX_POINTS = 50
REFRESH_SEC = 0.5
DASH_PORT = 8061

# --- Layout helpers (keeps graphs compact) ---
CONTAINER_STYLE = {
    "fontFamily": "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
    "padding": "12px",
    "maxWidth": "1400px",     # keeps content from stretching too wide
    "margin": "0 auto",       # center on page
}

ROW_2COL = {"display": "grid", "gridTemplateColumns": "repeat(2, minmax(0, 1fr))", "gap": "12px"}
ROW_1COL = {"display": "grid", "gridTemplateColumns": "1fr", "gap": "12px"}

def tidy(fig, title, ytitle):
    fig.update_layout(
        title=title,
        xaxis_title="Sim step",
        yaxis_title=ytitle,
        height=320,                          # <= consistent compact height
        margin=dict(l=40, r=10, t=40, b=35), # <= tighter margins
        legend=dict(orientation="h", y=-0.25, x=0),  # horizontal legend below
        template="plotly_white",
    )
    return fig


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
        self._ue_dl_prb_req = defaultdict(_deque)   # <-- NEW: requested PRBs

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
        
        self._prb_cap = None  # None = unlimited; or int for a live cap
        self.w_embb = None
        self.w_urllc = None
        self.w_mmtc = None


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
                    
                    # robustly handle absence on early steps
                    dl_req_map = getattr(cell, "dl_total_prb_demand", {}) or {}
                    dl_requested = dl_req_map.get(imsi, None)
                    if dl_requested is not None:
                        self._ue_dl_prb_req[imsi].append(float(dl_requested))

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
        
        app.layout = html.Div(
        style=CONTAINER_STYLE,
        children=[
            html.H2("Live RAN KPI Dashboard"),
            html.P("Streaming KPIs directly from UEs/Cells within the simulator."),

            # Controls row (keep it narrow so it doesn't force horizontal scroll)
            html.Div(
                style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "12px", "marginTop": "8px"},
                children=[
                    html.Div([
                        html.Label("Max DL PRBs per UE (live)"),
                        #dcc.Slider(
                        #id="prb-cap", min=0, max=50, step=1, value=10,
                        #tooltip={"always_visible": False},
                        #marks={0:"0",10:"10",20:"20",30:"30",40:"40",50:"50"},
                        #),
                        dcc.Slider(
                        id="prb-cap",
                        min=0,
                        max=RAN_PRB_CAP_SLIDER_MAX,
                        step=1,
                        value=(RAN_PRB_CAP_SLIDER_DEFAULT or 0),  # if None, show 0; your callback can treat 0 as "unlimited" if you want
                        tooltip={"always_visible": False},
                        marks={0: "0", 10: "10", 20: "20", 30: "30", 40: "40", 50: "50", RAN_PRB_CAP_SLIDER_MAX: str(RAN_PRB_CAP_SLIDER_MAX)},
                    ), 
                        html.Small("Set lower to throttle any single UE (unlimited = None)."),
                    ]),
                    html.Div(id="prb-cap-label", style={"alignSelf": "center"}),
            ],
            ),
            
            
            # --- Slice share controls (eMBB / URLLC / mMTC) ---
            html.Div(style={"display": "grid",
                "gridTemplateColumns": "1fr 1fr",
                "gap": "12px",
                "marginTop": "8px"}, children=[
            html.Div([
                html.Label("Slice shares (sum ≈ 100%)"),
                html.Div(style={"display": "grid", "gridTemplateColumns": "1fr", "gap": "8px"}, children=[
                html.Div([
                    html.Label("eMBB"),
                    dcc.Slider(id="w-embb",  min=0, max=100, step=1, value=60,marks={0:"0",25:"25",50:"50",75:"75",100:"100"},tooltip={"always_visible": True}),
                ]),
                html.Div([
                    html.Label("URLLC"),
                    dcc.Slider(id="w-urllc", min=0, max=100, step=1, value=30,marks={0:"0",25:"25",50:"50",75:"75",100:"100"},tooltip={"always_visible": True}),
                ]),
                html.Div([
                    html.Label("mMTC"),
                    dcc.Slider(id="w-mmtc",  min=0, max=100, step=1, value=10,marks={0:"0",25:"25",50:"50",75:"75",100:"100"},tooltip={"always_visible": True}),
                        ]),
                    ]),
                ]),
                html.Div(id="slice-weight-label", style={"alignSelf": "center", "fontWeight": 500}),
            ]),


            html.Hr(),

            html.Div(style=ROW_1COL, children=[ dcc.Graph(id="ue-bitrate") ]),

            html.Div(style=ROW_2COL, children=[
                dcc.Graph(id="ue-sinr"),
                dcc.Graph(id="ue-cqi"),
            ]),

            html.Div(style=ROW_2COL, children=[
                dcc.Graph(id="ue-prb-granted"),
                dcc.Graph(id="ue-prb-requested"),
            ]),

            html.Div(style=ROW_1COL, children=[ dcc.Graph(id="cell-load") ]),
            html.Div(style=ROW_1COL, children=[ dcc.Graph(id="ue-buffer") ]),

            dcc.Interval(id="tick", interval=int(REFRESH_SEC * 1000), n_intervals=0),
            ],
        )

        @app.callback(
            Output("prb-cap-label", "children"),
            Input("prb-cap", "value"),
            )
        def _set_cap(val):
            with self._lock:
                # None/unlimited handling: you can decide a sentinel; here keep int
                self._prb_cap = int(val) if val is not None else None
                for cell in self.cell_list.values():
                    cell.prb_per_ue_cap = self._prb_cap
                return f"Current cap: {self._prb_cap if self._prb_cap is not None else 'unlimited'} PRBs/UE"

        @app.callback(
            #Output("ue-bitrate", "figure"),
            #Output("ue-sinr-cqi", "figure"),
            Output("ue-bitrate", "figure"),
            Output("ue-sinr", "figure"),
            Output("ue-cqi", "figure"),
            Output("ue-prb-granted", "figure"),
            Output("ue-prb-requested", "figure"),
            Output("cell-load", "figure"),
            Output("ue-buffer", "figure"),
            Input("tick", "n_intervals"),
        )
        
        
        @app.callback(
            Output("slice-weight-label", "children"),
            Input("w-embb",  "value"),
            Input("w-urllc", "value"),
            Input("w-mmtc",  "value"),
        )
        def _set_slice_weights(w_embb, w_urllc, w_mmtc):
            if w_embb is None or w_urllc is None or w_mmtc is None:
                raise PreventUpdate

            # normalize to sum=1.0
            w = [max(0.0, float(w_embb)), max(0.0, float(w_urllc)), max(0.0, float(w_mmtc))]
            s = sum(w) or 1.0
            weights = {"eMBB": w[0]/s, "URLLC": w[1]/s, "mMTC": w[2]/s}

            # push into cells (the allocator will read this)
            with self._lock:
                for cell in self.cell_list.values():
                    cell.slice_weights = dict(weights)  # <-- keep this name consistent with cell.py

            pct = {k: f"{v*100:.1f}%" for k, v in weights.items()}
            return f"Effective slice shares → eMBB: {pct['eMBB']} | URLLC: {pct['URLLC']} | mMTC: {pct['mMTC']}"


        #def _update(_n, ue_filter, cell_filter):
        def _update(_n):
            with self._lock:
                tx = list(self._t)
                if not tx:
                    # Empty figures before first sample
                    return go.Figure(), go.Figure(), go.Figure(), go.Figure(), go.Figure()

                ue_keys = list(set(
                list(self._ue_dl_mbps.keys())
                + list(self._ue_sinr_db.keys())
                + list(self._ue_cqi.keys())
                + list(self._ue_dl_buf.keys())
                + list(self._ue_dl_prb.keys())
                + list(getattr(self, "_ue_dl_prb_req", {}).keys())  # if present
                ))

                cell_keys = list(set(
                list(self._cell_dl_load.keys())
                + list(self._cell_alloc_prb.keys())
                + list(self._cell_max_prb.keys())
                ))
                
                # --- UE bitrate (Mbps) ---
                tr_bitrate = []
                for imsi in ue_keys:
                    ys = list(self._ue_dl_mbps.get(imsi, []))
                    if ys:
                        tr_bitrate.append(go.Scatter(x=tx[-len(ys):], y=ys, mode="lines", name=f"{imsi} DL Mbps"))


                
                # --- UE SINR ---
                tr_sinr = []
                for imsi in ue_keys:
                    ys_s = list(self._ue_sinr_db.get(imsi, []))
                    if ys_s:
                        tr_sinr.append(go.Scatter(x=tx[-len(ys_s):], y=ys_s, mode="lines", name=f"{imsi} SINR (dB)"))
                
                #fig_sinr = go.Figure(data=tr_sinr, layout=go.Layout(title="Per-UE SINR", xaxis={"title": "Sim step"}, yaxis={"title": "SINR (dB)"}))

                # --- UE CQI ---
                tr_cqi = []
                for imsi in ue_keys:
                    ys_c = list(self._ue_cqi.get(imsi, []))
                    if ys_c:
                        tr_cqi.append(go.Scatter(x=tx[-len(ys_c):], y=ys_c, mode="lines", name=f"{imsi} CQI"))
                #fig_cqi = go.Figure(data=tr_cqi, layout=go.Layout(title="Per-UE CQI", xaxis={"title": "Sim step"}, yaxis={"title": "CQI"}))



                
                # --- UE DL PRBs: GRANTED (separate plot) ---
                tr_prb_granted = []
                for imsi in ue_keys:
                    ys_g = list(self._ue_dl_prb.get(imsi, []))
                    if ys_g:
                        tr_prb_granted.append(go.Scatter(
                        x=tx[-len(ys_g):], y=ys_g, mode="lines", name=f"{imsi} granted"
                        ))


                # --- UE DL PRBs: REQUESTED (separate plot) ---
                tr_prb_requested = []
                for imsi in ue_keys:
                    ys_r = list(getattr(self, "_ue_dl_prb_req", {}).get(imsi, []))
                    if ys_r:
                        tr_prb_requested.append(go.Scatter(
                        x=tx[-len(ys_r):], y=ys_r, mode="lines", name=f"{imsi} requested"
                        ))




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

                # --- UE DL buffer (optional) ---
                tr_buf = []
                for imsi in ue_keys:
                    ys = list(self._ue_dl_buf.get(imsi, []))
                    if ys:
                        tr_buf.append(go.Scatter(x=tx[-len(ys):], y=ys, mode="lines", name=f"{imsi} DL buffer (bytes)"))

            
            fig_bitrate = tidy(go.Figure(data=tr_bitrate), "Per‑UE Downlink Bitrate (Mbps)", "Mbps")

            fig_sinr = tidy(go.Figure(data=tr_sinr), "Per‑UE SINR", "SINR (dB)")
            fig_cqi  = tidy(go.Figure(data=tr_cqi),  "Per‑UE CQI",  "CQI")

            fig_prb_granted = tidy(go.Figure(data=tr_prb_granted), "Per‑UE DL PRBs — GRANTED", "PRBs")
            fig_prb_requested = tidy(go.Figure(data=tr_prb_requested), "Per‑UE DL PRBs — REQUESTED", "PRBs")

            fig_cell = tidy(go.Figure(data=tr_cell), "Per‑Cell Load & PRBs", "Value / PRBs")
            fig_buf  = tidy(go.Figure(data=tr_buf),  "Per‑UE DL Buffer (bytes)*", "Bytes")

            #return fig_bitrate, fig_sinr_cqi, fig_prb, fig_cell, fig_buf
            return  fig_bitrate, fig_sinr, fig_cqi, fig_prb_granted, fig_prb_requested, fig_cell,fig_buf



        def _run():
            app.run_server(host="127.0.0.1", port=DASH_PORT, debug=False)

        self._dash_thread = threading.Thread(target=_run, daemon=True)
        self._dash_thread.start()
        print(f"{self.xapp_id}: live KPI dashboard at http://localhost:{DASH_PORT}")
