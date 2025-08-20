# network_layer/xApps/xapp_KPI_logger.py
import csv
import hashlib
import os
import time
from .xapp_base import xAppBase

class xAppKpiLogger(xAppBase):
    """
    Per-step UE KPI collector. Writes one row per (UE, step).
    """

    def __init__(self, ric=None, out_dir="logs", filename_prefix="kpis"):
        super().__init__(ric=ric)
        self.enabled = True
        self.out_dir = out_dir
        os.makedirs(self.out_dir, exist_ok=True)
        self.filepath = os.path.join(self.out_dir, f"{filename_prefix}_{int(time.time())}.csv")
        self._writer = None
        self._file = None
        self._header = [
            "Timestamp","num_ues","IMSI","RNTI",
            "slicing_enabled","slice_id","slice_prb","power_multiplier","scheduling_policy",
            "dl_mcs","dl_n_samples","dl_buffer [bytes]","tx_brate downlink [Mbps]","tx_pkts downlink","tx_errors downlink (%)","dl_cqi",
            "ul_mcs","ul_n_samples","ul_buffer [bytes]","rx_brate uplink [Mbps]","rx_pkts uplink","rx_errors uplink (%)",
            "ul_rssi","ul_sinr","phr",
            "sum_requested_prbs","sum_granted_prbs",
            "dl_pmi","dl_ri","ul_n","ul_turbo_iters"
        ]

    def start(self):
        # open CSV with header once
        self._file = open(self.filepath, "w", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=self._header)
        self._writer.writeheader()

    def stop(self):
        if self._file:
            self._file.flush()
            self._file.close()
            self._file = None

    def _rnti_from_imsi(self, imsi: str) -> int:
        # stable 16-bit pseudo RNTI
        return int(hashlib.sha1(imsi.encode()).hexdigest(), 16) & 0xFFFF

    def step(self):
        if not self.enabled or self._writer is None:
            return
        sim = self.ric.simulation_engine
        t = sim.sim_step

        for ue in self.ue_list.values():
            cell = ue.current_cell
            if cell is None:
                continue

            # cell-level aggregates
            num_ues = len(cell.connected_ue_list)
            sum_granted_prbs = cell.allocated_dl_prb  # total DL PRBs granted this step

            # per-UE allocation (0 if UE not in dict yet)
            ue_alloc = cell.prb_ue_allocation_dict.get(ue.ue_imsi, {"downlink": 0, "uplink": 0})
            slice_prb = ue_alloc.get("downlink", 0)

            # uplink RSSI measured at gNB (dBm) — set by cell.monitor_ue_signal_strength
            ul_rssi = cell.ue_uplink_signal_strength_dict.get(ue.ue_imsi, None)

            row = {
                "Timestamp": t,
                "num_ues": num_ues,
                "IMSI": ue.ue_imsi,
                "RNTI": self._rnti_from_imsi(ue.ue_imsi),
                "slicing_enabled": ue.slice_type is not None,
                "slice_id": ue.slice_type,
                "slice_prb": slice_prb,
                "power_multiplier": 1.0,                       # not modeled yet
                "scheduling_policy": "QoS-PFS",                 # matches current allocator
                "dl_mcs": ue.downlink_mcs_index,
                "dl_n_samples": None,                           # not modeled yet
                "dl_buffer [bytes]": None,                      # not modeled yet
                "tx_brate downlink [Mbps]": ue.downlink_bitrate / 1e6 if ue.downlink_bitrate else 0.0,
                "tx_pkts downlink": None,                       # not modeled yet
                "tx_errors downlink (%)": None,                 # not modeled yet
                "dl_cqi": ue.downlink_cqi,
                "ul_mcs": None,                                 # not modeled yet
                "ul_n_samples": None,                           # not modeled yet
                "ul_buffer [bytes]": None,                      # not modeled yet
                "rx_brate uplink [Mbps]": None,                 # not modeled yet
                "rx_pkts uplink": None,                         # not modeled yet
                "rx_errors uplink (%)": None,                   # not modeled yet
                "ul_rssi": ul_rssi,
                "ul_sinr": None,                                # not modeled yet
                "phr": None,                                    # not modeled yet
                "sum_requested_prbs": None,                     # not persisted yet
                "sum_granted_prbs": sum_granted_prbs,
                "dl_pmi": None,
                "dl_ri": None,
                "ul_n": None,
                "ul_turbo_iters": None,
            }
            self._writer.writerow(row)

    def to_json(self):
        res = super().to_json()
        res["output_csv"] = self.filepath
        return res
