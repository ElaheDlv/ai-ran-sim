import math
import settings
from utils import dist_between, estimate_throughput


class Cell:
    def __init__(self, base_station, cell_init_data):
        assert base_station is not None, "Base station cannot be None"
        assert cell_init_data is not None, "Cell init data cannot be None"
        self.base_station = base_station

        self.cell_id = cell_init_data["cell_id"]
        self.frequency_band = cell_init_data["frequency_band"]
        self.carrier_frequency_MHz = cell_init_data["carrier_frequency_MHz"]
        self.bandwidth_Hz = cell_init_data["bandwidth_Hz"]
        self.max_prb = cell_init_data["max_prb"]
        self.max_dl_prb = cell_init_data["max_dl_prb"]
        self.max_ul_prb = cell_init_data["max_ul_prb"]
        self.cell_radius = cell_init_data["cell_radius"]
        self.transmit_power_dBm = cell_init_data["transmit_power_dBm"]
        self.cell_individual_offset_dBm = cell_init_data["cell_individual_offset_dBm"]
        self.frequency_priority = cell_init_data["frequency_priority"]
        self.qrx_level_min = cell_init_data["qrx_level_min"]

        self.prb_ue_allocation_dict = {}  # { "ue_imsi": {"downlink": 30, "uplink": 5}}
        self.connected_ue_list = {}
        self.ue_uplink_signal_strength_dict = {}
        self.scheduler_policy = cell_init_data.get("scheduler_policy", "QoS-aware PFS")
        
        # cell.py __init__
        self.dl_total_prb_demand = {}            # {imsi: int}
        self.dl_throughput_per_prb_map = {}      # {imsi: float}
        
        # Limit for max DL PRBs any single UE can get (None = no cap)
        #self.prb_per_ue_cap = None
        self.prb_per_ue_cap = settings.RAN_PRB_PER_UE_CAP 
        
        #self.slice_shares = {"eMBB": 0.5, "URLLC": 0.3, "mMTC": 0.2}  # default
        #self.slice_caps   = {"eMBB": None, "URLLC": None, "mMTC": None}  # optional per-slice cap
        
        # Default (normalized) slice weights; xApp will override live
        #self.slice_weights = {"eMBB": 0.6, "URLLC": 0.3, "mMTC": 0.1}
        
        try:
            from settings.ran_config import RAN_DEFAULT_SLICE_WEIGHTS
            self.slice_weights = dict(RAN_DEFAULT_SLICE_WEIGHTS)
        except Exception:
            self.slice_weights = {"eMBB": 0.6, "URLLC": 0.3, "mMTC": 0.1}
    
        # Keep your existing per-UE cap mechanism if present
        self.prb_per_ue_cap = getattr(self, "prb_per_ue_cap", None)



    def __repr__(self):
        return f"Cell({self.cell_id}, base_station={self.base_station.bs_id}, frequency_band={self.frequency_band}, carrier_frequency_MHz={self.carrier_frequency_MHz})"

    
    def slice_budget(self, slice_name):
        share = self.slice_shares.get(slice_name, 0.0)
        return int(max(0, share) * int(self.max_dl_prb))


    @property
    def allocated_dl_prb(self):
        return sum(
            [
                self.prb_ue_allocation_dict[ue_imsi]["downlink"]
                for ue_imsi in self.connected_ue_list.keys()
            ]
        )

    @property
    def allocated_ul_prb(self):
        return sum(
            [
                self.prb_ue_allocation_dict[ue_imsi]["uplink"]
                for ue_imsi in self.connected_ue_list.keys()
            ]
        )

    @property
    def allocated_prb(self):
        return sum(
            [
                self.prb_ue_allocation_dict[ue_imsi]["uplink"]
                + self.prb_ue_allocation_dict[ue_imsi]["downlink"]
                for ue_imsi in self.connected_ue_list.keys()
            ]
        )

    @property
    def current_load(self):
        return self.allocated_prb / self.max_prb

    @property
    def current_dl_load(self):
        return self.allocated_dl_prb / self.max_dl_prb

    @property
    def current_ul_load(self):
        return self.allocated_ul_prb / self.max_ul_prb

    @property
    def position_x(self):
        return self.base_station.position_x

    @property
    def position_y(self):
        return self.base_station.position_y

    def register_ue(self, ue):
        self.connected_ue_list[ue.ue_imsi] = ue
        self.prb_ue_allocation_dict[ue.ue_imsi] = {
            "downlink": 0,
            "uplink": 0,
        }

    def monitor_ue_signal_strength(self):
        self.ue_uplink_signal_strength_dict = {}
        pass_loss_model = settings.CHANNEL_PASS_LOSS_MODEL_MAP[
            settings.CHANNEL_PASS_LOSS_MODEL_URBAN_MACRO_NLOS
        ]
        # monitor the ue uplink signal strength
        for ue in self.connected_ue_list.values():
            # calculate the received power based on distance and transmit power
            distance = dist_between(
                self.position_x,
                self.position_y,
                ue.position_x,
                ue.position_y,
            )
            received_power = ue.uplink_transmit_power_dBm - pass_loss_model(
                distance_m=distance, frequency_ghz=self.carrier_frequency_MHz / 1000
            )
            self.ue_uplink_signal_strength_dict[ue.ue_imsi] = received_power

    def select_ue_mcs(self):
        for ue in self.connected_ue_list.values():
            ue.set_downlink_mcs_index(-1)
            ue.set_downlink_mcs_data(None)
            ue_cqi_mcs_data = settings.UE_CQI_MCS_SPECTRAL_EFFICIENCY_TABLE.get(
                ue.downlink_cqi, None
            )
            if ue.downlink_cqi == 0 or ue_cqi_mcs_data is None:
                continue

            ue_cqi_eff = ue_cqi_mcs_data["spectral_efficiency"]
            max_mcs_index = 0
            for (
                mcs_index,
                mcs_eff,
            ) in settings.RAN_MCS_SPECTRAL_EFFICIENCY_TABLE.items():
                if mcs_eff["spectral_efficiency"] <= ue_cqi_eff:
                    max_mcs_index = mcs_index
                else:
                    break
            ue.set_downlink_mcs_index(max_mcs_index)
            downlink_mcs_data = settings.RAN_MCS_SPECTRAL_EFFICIENCY_TABLE.get(
                max_mcs_index, None
            )
            if downlink_mcs_data is None:
                ue.set_downlink_mcs_data(None)
            else:
                # copy the dictionary to avoid modifying the original data
                ue.set_downlink_mcs_data(downlink_mcs_data.copy())

    def step(self, delta_time):
        self.monitor_ue_signal_strength()

        # select modulation and coding scheme (MCS) for each UE based on CQI
        self.select_ue_mcs()

        # allocate PRBs dynamically based on each UE's QoS profile and channel conditions
        self.allocate_prb()

        # for each UE, estimate the downlink, uplink bitrate and latency
        #self.estimate_ue_bitrate_and_latency()
        self.estimate_ue_bitrate_and_latency(delta_time)


    '''    
    def allocate_prb(self):
        # QoS-aware Proportional Fair Scheduling (PFS) with optional per-UE cap

        # reset PRB allocation for all UEs
        for ue in self.connected_ue_list.values():
            self.prb_ue_allocation_dict[ue.ue_imsi]["downlink"] = 0
            self.prb_ue_allocation_dict[ue.ue_imsi]["uplink"] = 0

        ue_prb_requirements = {}
        
        # ---- Step 1: per-UE PRB demand (from GBR & MCS)
        for ue in self.connected_ue_list.values():
            dl_gbr = ue.qos_profile["GBR_DL"]
            dl_mcs = ue.downlink_mcs_data
            if dl_mcs is None:
                # no usable MCS → skip this UE for this step
                print(
                    f"Cell {self.cell_id}: UE {ue.ue_imsi} has no downlink MCS data. Skipping."
                )
                continue
            dl_throughput_per_prb = estimate_throughput(
                dl_mcs["modulation_order"], dl_mcs["target_code_rate"], 1
            )
            dl_required_prbs = max(0, math.ceil(dl_gbr / dl_throughput_per_prb))
            ue_prb_requirements[ue.ue_imsi] = {
                "dl_required_prbs": dl_required_prbs,
                "dl_throughput_per_prb": dl_throughput_per_prb,
            }

        # Persist for KPI/xApp consumption
        self.dl_total_prb_demand = {imsi: d["dl_required_prbs"] for imsi, d in ue_prb_requirements.items()}
        self.dl_throughput_per_prb_map = {imsi: d["dl_throughput_per_prb"] for imsi, d in ue_prb_requirements.items()}

        if not ue_prb_requirements:
            return  # nothing to allocate this step

        cap = self.prb_per_ue_cap  # None or an integer

        # ---- Step 2: compute weights = min(request, cap) and allocate proportionally
        # Desired (capped) demand per UE
        desired = {
            imsi: (min(d["dl_required_prbs"], int(cap)) if (cap is not None) else d["dl_required_prbs"])
            for imsi, d in ue_prb_requirements.items()
        }

        total_desired = sum(desired.values())
        budget = int(self.max_dl_prb)

        if total_desired == 0 or budget <= 0:
            # Nothing requested or no capacity
            return

        if total_desired <= budget:
            # We can satisfy everyone up to their capped demand
            for imsi, want in desired.items():
                self.prb_ue_allocation_dict[imsi]["downlink"] = int(want)
            return

        # Proportional share with rounding, respecting cap
        # First pass: floor allocation
        alloc = {}
        remainders = []
        for imsi, want in desired.items():
            share_float = budget * (want / total_desired)
            base = int(math.floor(share_float))
            alloc[imsi] = min(base, want)  # never exceed per-UE cap (want)
            # Track remainder for tie‑breaking pass (bigger remainder gets the leftover PRBs)
            remainders.append((share_float - base, imsi, want))

        used = sum(alloc.values())
        leftover = max(0, budget - used)

        # Second pass: distribute leftover PRBs by largest fractional remainder,
        # but do not exceed each UE's desired cap.
        remainders.sort(reverse=True)  # highest remainder first
        for _, imsi, want in remainders:
            if leftover <= 0:
                break
            if alloc[imsi] < want:
                alloc[imsi] += 1
                leftover -= 1

        for imsi, a in alloc.items():
            self.prb_ue_allocation_dict[imsi]["downlink"] = int(a)
            
    '''
    def allocate_prb(self):
        # QoS-aware PF with optional per-UE cap and slice shares

        # Reset PRBs
        for ue in self.connected_ue_list.values():
            self.prb_ue_allocation_dict[ue.ue_imsi]["downlink"] = 0
            self.prb_ue_allocation_dict[ue.ue_imsi]["uplink"] = 0

        # ---- Step 1: per-UE demand from GBR + MCS
        ue_prb_requirements = {}
        for ue in self.connected_ue_list.values():
            dl_gbr = getattr(ue, "qos_profile", {}).get("GBR_DL", 0.0)
            dl_mcs = getattr(ue, "downlink_mcs_data", None)
            if not dl_mcs:
                # No usable MCS → skip demand this step
                # print(f"Cell {self.cell_id}: UE {ue.ue_imsi} missing MCS")
                continue
            dl_tput_per_prb = estimate_throughput(
                dl_mcs["modulation_order"], dl_mcs["target_code_rate"], 1
            )
            want = int(max(0, math.ceil((dl_gbr or 0.0) / max(dl_tput_per_prb, 1e-9))))
            ue_prb_requirements[ue.ue_imsi] = {
                "slice": getattr(ue, "slice_type", "eMBB"),  # default to eMBB
                "want": want,
                "tput_per_prb": dl_tput_per_prb,
            }

        # Persist for xApp KPIs
        self.dl_total_prb_demand = {imsi: d["want"] for imsi, d in ue_prb_requirements.items()}
        self.dl_throughput_per_prb_map = {imsi: d["tput_per_prb"] for imsi, d in ue_prb_requirements.items()}

        if not ue_prb_requirements:
            self.allocated_dl_prb = 0
            return

        # ---- Step 2: slice budgets
        budget = int(getattr(self, "max_dl_prb", 0))
        weights = dict(getattr(self, "slice_weights", {})) or {"eMBB": 1.0}
        # normalize in case someone set odd values
        ws = sum(max(0.0, v) for v in weights.values()) or 1.0
        for k in list(weights.keys()):
            weights[k] = max(0.0, float(weights[k])) / ws

        # Which slices actually have UEs this step?
        slice_to_imsis = collections.defaultdict(list)
        for imsi, d in ue_prb_requirements.items():
            slice_to_imsis[d["slice"]].append(imsi)

        # Weighted budgets per slice (largest remainders for rounding)
        raw = {s: budget * weights.get(s, 0.0) for s in weights}
        base = {s: int(math.floor(v)) for s, v in raw.items()}
        rems = sorted([(raw[s] - base[s], s) for s in weights], reverse=True)
        slice_budget = dict(base)
        leftover = budget - sum(base.values())
        for _, s in rems:
            if leftover <= 0:
                break
            slice_budget[s] += 1
            leftover -= 1

        # ---- Step 3: per-slice allocation with per-UE cap
        cap = getattr(self, "prb_per_ue_cap", None)
        alloc = {imsi: 0 for imsi in ue_prb_requirements}
        global_leftover = 0

        for s, imsis in slice_to_imsis.items():
            B = int(slice_budget.get(s, 0))
            if B <= 0:
                continue

            # desired = min(want, cap) if cap set
            desired = {}
            for imsi in imsis:
                want = ue_prb_requirements[imsi]["want"]
                desired[imsi] = min(want, int(cap)) if cap is not None else want

            total_desired = sum(desired.values())
            if total_desired == 0:
                global_leftover += B
                continue

            if total_desired <= B:
                # can satisfy all in this slice
                for imsi, want in desired.items():
                    alloc[imsi] += int(want)
                # leftover slice PRBs become global
                global_leftover += B - total_desired
                continue

            # proportional floor + remainders (respect cap)
            remainders = []
            used = 0
            base_alloc = {}
            for imsi, want in desired.items():
                share_f = B * (want / total_desired)
                b = int(math.floor(share_f))
                base_alloc[imsi] = min(b, want)
                used += base_alloc[imsi]
                remainders.append((share_f - b, imsi, want))

            L = max(0, B - used)
            remainders.sort(reverse=True)
            for _, imsi, want in remainders:
                if L <= 0:
                    break
                if base_alloc[imsi] < want:
                    base_alloc[imsi] += 1
                    L -= 1

            for imsi, a in base_alloc.items():
                alloc[imsi] += int(a)

        # ---- Step 4: redistribute any global leftover to UEs that still have headroom
        if global_leftover > 0:
            # room = desired - alloc (recompute desired with cap)
            room = {}
            total_room = 0
            for imsi, d in ue_prb_requirements.items():
                want = d["want"]
                desired = min(want, int(cap)) if cap is not None else want
                r = max(0, desired - alloc[imsi])
                room[imsi] = r
                total_room += r

            if total_room > 0:
                # proportional to remaining room
                rema = []
                add = {imsi: 0 for imsi in room}
                used = 0
                for imsi, r in room.items():
                    share_f = global_leftover * (r / total_room) if total_room > 0 else 0
                    b = int(math.floor(share_f))
                    add[imsi] = min(b, r)
                    used += add[imsi]
                    rema.append((share_f - b, imsi, r))

                L = max(0, global_leftover - used)
                rema.sort(reverse=True)
                for _, imsi, r in rema:
                    if L <= 0:
                        break
                    if add[imsi] < r:
                        add[imsi] += 1
                        L -= 1

                for imsi, inc in add.items():
                    alloc[imsi] += inc
            # else: nobody can take more → drop leftover

        # ---- Commit
        total_alloc = 0
        for imsi, a in alloc.items():
            self.prb_ue_allocation_dict[imsi]["downlink"] = int(a)
            total_alloc += int(a)
        self.allocated_dl_prb = int(total_alloc)


        # # Logging
        # for ue_imsi, allocation in self.prb_ue_allocation_dict.items():
        #     print(
        #         f"Cell: {self.cell_id} allocated {allocation['downlink']} DL PRBs for UE {ue_imsi}"
        #     )
    
    def estimate_ue_bitrate_and_latency(self, delta_time):
        for ue in self.connected_ue_list.values():
            if ue.downlink_mcs_data is None:
                print(f"Cell {self.cell_id}: UE {ue.ue_imsi} has no downlink MCS data. Skipping.")
                continue

            ue_modulation_order = ue.downlink_mcs_data["modulation_order"]
            ue_code_rate = ue.downlink_mcs_data["target_code_rate"]
            ue_dl_prb  = self.prb_ue_allocation_dict[ue.ue_imsi]["downlink"]

            # Achievable DL bitrate (bits/s)
            # TODO: uplink bitrate
            dl_bitrate = estimate_throughput(ue_modulation_order, ue_code_rate, ue_dl_prb)
            ue.set_downlink_bitrate(dl_bitrate)
            # TODO: downlink and uplink latency

            # Optional: drain a per-UE buffer if you added ue.dl_buffer_bytes
            if hasattr(ue, "dl_buffer_bytes"):
                bytes_can_send   = (dl_bitrate / 8.0) * delta_time
                transmitted      = min(bytes_can_send, ue.dl_buffer_bytes)
                ue.dl_buffer_bytes -= transmitted

                # Simple queueing-delay proxy (seconds). Set 0 if no capacity.
                ue.downlink_latency = (ue.dl_buffer_bytes / (dl_bitrate / 8.0)) if dl_bitrate > 0 else 0.0

    def deregister_ue(self, ue):
        if ue.ue_imsi in self.prb_ue_allocation_dict:
            del self.prb_ue_allocation_dict[ue.ue_imsi]
            print(f"Cell {self.cell_id}: Released resources for UE {ue.ue_imsi}")
        else:
            print(f"Cell {self.cell_id}: No resources to release for UE {ue.ue_imsi}")

        if ue.ue_imsi in self.connected_ue_list:
            del self.connected_ue_list[ue.ue_imsi]
            print(f"Cell {self.cell_id}: Deregistered UE {ue.ue_imsi}")
        else:
            print(f"Cell {self.cell_id}: No UE {ue.ue_imsi} to deregister")

    def to_json(self):
        return {
            "cell_id": self.cell_id,
            "frequency_band": self.frequency_band,
            "carrier_frequency_MHz": self.carrier_frequency_MHz,
            "bandwidth_Hz": self.bandwidth_Hz,
            "max_prb": self.max_prb,
            "cell_radius": self.cell_radius,
            "vis_cell_radius": self.cell_radius
            * settings.REAL_LIFE_DISTANCE_MULTIPLIER,
            "position_x": self.position_x,
            "position_y": self.position_y,
            "vis_position_x": self.position_x * settings.REAL_LIFE_DISTANCE_MULTIPLIER,
            "vis_position_y": self.position_y * settings.REAL_LIFE_DISTANCE_MULTIPLIER,
            "prb_ue_allocation_dict": self.prb_ue_allocation_dict,
            "max_dl_prb": self.max_dl_prb,
            "max_ul_prb": self.max_ul_prb,
            "allocated_dl_prb": self.allocated_dl_prb,
            "allocated_ul_prb": self.allocated_ul_prb,
            "current_dl_load": self.allocated_dl_prb / self.max_dl_prb,
            "current_ul_load": self.allocated_ul_prb / self.max_ul_prb,
            "current_load": self.current_load,
            "connected_ue_list": list(self.connected_ue_list.keys()),
        }
