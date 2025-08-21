# network_layer/xApps/xapp_kpi_collector.py
import csv, time
from .xapp_base import xAppBase

class xAppKPICollector(xAppBase):
    def __init__(self, ric=None, out_path="kpis.csv"):
        super().__init__(ric=ric)
        self.enabled = True
        self.out_path = out_path
        self._wrote_header = False

        self.fields = [
            "Timestamp","num_ues","IMSI","RNTI",
            "slicing_enabled","slice_id","slice_prb","power_multiplier","scheduling_policy",
            "dl_mcs","dl_n_samples","dl_buffer_bytes","last_new_dl_bytes","downlink_latency_ms","tx_brate_downlink_Mbps",
            "tx_pkts_downlink","tx_errors_downlink_pct","dl_cqi",
            "ul_mcs","ul_n_samples","ul_buffer_bytes","rx_brate_uplink_Mbps",
            "rx_pkts_uplink","rx_errors_uplink_pct","ul_rssi","ul_sinr","phr",
            "sum_requested_prbs","sum_granted_prbs",
            "dl_pmi","dl_ri","ul_n","ul_turbo_iters",
        ]

    def start(self):
        pass

    def _row_for_ue(self, ue):
        # Prefer connected UEs in this serving cell for num_ues
        cell = ue.current_cell
        bs   = ue.current_bs
        if cell:
            num_ues = len(cell.connected_ue_list)
        else:
            num_ues = len(self.ue_list)  # fallback

        # Scheduling policy + PRBs
        scheduling_policy = getattr(cell, "scheduler_policy", None) if cell else None
        prb_alloc = cell.prb_ue_allocation_dict.get(ue.ue_imsi, {"downlink":0,"uplink":0}) if cell else {"downlink":0,"uplink":0}
        dl_granted = prb_alloc["downlink"]

        dl_requested = None
        if cell and hasattr(cell, "dl_total_prb_demand"):
            print(f"Cell {cell.cell_id} has dl_total_prb_demand: {cell.dl_total_prb_demand}")
            dl_requested = cell.dl_total_prb_demand.get(ue.ue_imsi, None)
        else:
            dl_requested = 0   
            print(f"Cell {cell.cell_id} has no dl_total_prb_demand")

        # Rates (Mbps)
        tx_brate_mbps = (ue.downlink_bitrate or 0) / 1e6
        rx_brate_mbps = None  # UL not modeled

        # UL RSSI (dBm) from serving cell monitor (cell stores per-UE uplink signal pow)
        ul_rssi = None
        if cell and hasattr(cell, "ue_uplink_signal_strength_dict"):
            ul_rssi = cell.ue_uplink_signal_strength_dict.get(ue.ue_imsi, None)

        # Slicing info
        slicing_enabled = ue.slice_type is not None
        slice_id = ue.slice_type
        slice_prb = dl_granted  # proxy for DL share

        # MCS/CQI
        dl_mcs = ue.downlink_mcs_index
        dl_cqi = ue.downlink_cqi

        # Buffer + latency
        dl_buffer_bytes = getattr(ue, "dl_buffer_bytes", None)
        last_new_dl_bytes = getattr(ue, "last_new_dl_bytes", None)
        downlink_latency_ms = (ue.downlink_latency or 0) * 1000.0  # if you store seconds

        placeholders = dict(
            RNTI=None, power_multiplier=None,
            dl_n_samples=None,
            tx_pkts_downlink=None, tx_errors_downlink_pct=None,
            ul_mcs=None, ul_n_samples=None, ul_buffer_bytes=None,
            rx_pkts_uplink=None, rx_errors_uplink_pct=None,
            ul_sinr=None, phr=None, dl_pmi=None, dl_ri=None, ul_n=None, ul_turbo_iters=None,
        )

        return {
            "Timestamp": int(time.time()*1000),
            "num_ues": num_ues,
            "IMSI": ue.ue_imsi,
            **placeholders,
            "slicing_enabled": slicing_enabled,
            "slice_id": slice_id,
            "slice_prb": slice_prb,
            "scheduling_policy": scheduling_policy,
            "dl_mcs": dl_mcs,
            "dl_buffer_bytes": dl_buffer_bytes,
            "last_new_dl_bytes": last_new_dl_bytes,
            "downlink_latency_ms": downlink_latency_ms,
            "tx_brate_downlink_Mbps": tx_brate_mbps,
            "dl_cqi": dl_cqi,
            "ul_rssi": ul_rssi,
            "sum_requested_prbs": dl_requested,
            "sum_granted_prbs": dl_granted,
        }

    def step(self):
        if not self.enabled:
            return
        rows = []
        for ue in self.ue_list.values():
            if not ue.connected:
                continue
            rows.append(self._row_for_ue(ue))
        if not rows:
            return

        with open(self.out_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.fields)
            if not self._wrote_header:
                writer.writeheader()
                self._wrote_header = True
            writer.writerows(rows)

    def to_json(self):
        j = super().to_json()
        j["out_path"] = self.out_path
        j["fields"] = self.fields
        return j
