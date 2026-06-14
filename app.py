import streamlit as st
import pandas as pd
import numpy as np
import time

st.set_page_config(layout="wide")

# --- 1. CLOCK FORMATTING HELPERS ---
def format_to_mmss(total_seconds):
    minutes = int(total_seconds) // 60
    seconds = int(total_seconds) % 60
    return f"{minutes:02d}:{seconds:02d}"

def parse_mmss_to_seconds(mmss_str):
    try:
        parts = mmss_str.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except:
        return 0

# --- 2. INITIALIZE GLOBAL STATE ---
SHIFT_MAX_SECONDS = 480 * 60  # 480 Minutes = 28,800 Seconds

if "sim_time" not in st.session_state:
    st.session_state.sim_time = 0          
    st.session_state.trip_log = []         
    st.session_state.starvation_events = {}  
    st.session_state.tugger_status = "Idle at Start Hub"
    st.session_state.process_timer = -1   
    st.session_state.max_transit_secs = 1
    st.session_state.trip_counter = 0      
    st.session_state.running = False       
    st.session_state.tugger_pct = 0.0      
    st.session_state.trip_start_time = 0   
    st.session_state.active_delivery_qty = 0
    st.session_state.current_target_point = None

    # Base Layout Configurations
    st.session_state.workstations = {
        "RA140": {"lane": "top",    "sequence_order": 4, "sub_stations": {"Point A": {"inventory": 24, "rop": 12, "qty_per_pkg": 12, "pkgs_per_trip": 1, "distance_meters": 150}}},
        "RA130": {"lane": "top",    "sequence_order": 3, "sub_stations": {"Point A": {"inventory": 24, "rop": 12, "qty_per_pkg": 12, "pkgs_per_trip": 1, "distance_meters": 250}}},
        "RA120": {"lane": "top",    "sequence_order": 2, "sub_stations": {"Point A": {"inventory": 24, "rop": 12, "qty_per_pkg": 12, "pkgs_per_trip": 1, "distance_meters": 350}}},
        "RA110": {"lane": "top",    "sequence_order": 1, "sub_stations": {"Point A": {"inventory": 6,  "rop": 3,  "qty_per_pkg": 3,  "pkgs_per_trip": 1, "distance_meters": 450}}},
        "RA170": {"lane": "bottom", "sequence_order": 5, "sub_stations": {"Point A": {"inventory": 24, "rop": 12, "qty_per_pkg": 12, "pkgs_per_trip": 1, "distance_meters": 600}}},
        "RA160": {"lane": "bottom", "sequence_order": 6, "sub_stations": {"Point A": {"inventory": 24, "rop": 12, "qty_per_pkg": 12, "pkgs_per_trip": 1, "distance_meters": 720}}},
        "RA150": {"lane": "bottom", "sequence_order": 7, "sub_stations": {"Point A": {"inventory": 24, "rop": 12, "qty_per_pkg": 12, "pkgs_per_trip": 1, "distance_meters": 840}}}
    }
    
    for ws_name, ws_data in st.session_state.workstations.items():
        for sub_name in ws_data["sub_stations"].keys():
            unique_key = f"{ws_name}_{sub_name}"
            st.session_state.starvation_events[unique_key] = 0

    snap = {f"{ws}_{sub}": d["inventory"] for ws in st.session_state.workstations.values() for sub, d in ws["sub_stations"].items()}
    st.session_state.chart_data = pd.DataFrame([snap])

# --- 3. SIDEBAR CONTROLS ---
st.sidebar.title("🎮 Factory Control Room")
app_mode = st.sidebar.selectbox("📂 Select Dashboard Page", ["🗺️ Live Simulation Map", "📊 Lineside Stock & Refill Analysis"])

st.sidebar.header("⚙️ Master Line Rate Settings")
master_takt_mins = st.sidebar.number_input("Whole Line Master Takt (Minutes)", min_value=1.0, value=8.0, step=0.5)
master_takt_secs = int(master_takt_mins * 60)

st.sidebar.header("🚜 Logistics Towing Properties")
speed_kmh = st.sidebar.number_input("Tugger Travel Speed (km/h)", min_value=1.0, max_value=30.0, value=12.0, step=0.5)
speed_ms = (speed_kmh * 1000.0) / 3600.0

# --- DYNAMIC NODE ADDITION/REMOVAL ---
st.sidebar.header("🛠️ Floorplan Modification Hub")
mod_action = st.sidebar.selectbox("Choose Structural Action:", ["Modify Station Data", "Add New Station/Drop Point", "Remove Existing Node"])

if mod_action == "Modify Station Data":
    flat_subs = [f"{ws_k} -> {sub_k}" for ws_k, ws_v in st.session_state.workstations.items() for sub_k in ws_v["sub_stations"].keys()]
    selected_flat = st.sidebar.selectbox("Select Station Drop to Edit:", flat_subs if flat_subs else ["None"])

    if selected_flat and selected_flat != "None":
        ws_target, sub_target = selected_flat.split(" -> ")
        pt_ref = st.session_state.workstations[ws_target]["sub_stations"][sub_target]
        
        st.markdown(f"⚙️ **Editing:** `{ws_target}`")
        st.session_state.workstations[ws_target]["sequence_order"] = st.sidebar.number_input("Production Start Seq Order:", min_value=1, max_value=20, value=int(st.session_state.workstations[ws_target]["sequence_order"]))
        st.session_state.workstations[ws_target]["lane"] = st.sidebar.selectbox("Visual Track Lane Line:", ["top", "bottom"], index=0 if st.session_state.workstations[ws_target]["lane"] == "top" else 1)
        pt_ref["distance_meters"] = st.sidebar.number_input("Meter Distance from Start Hub:", min_value=10, max_value=990, value=int(pt_ref["distance_meters"]))
        pt_ref["inventory"] = st.sidebar.number_input("Live Stock Level (Units)", min_value=0, value=int(pt_ref["inventory"]))
        pt_ref["rop"] = st.sidebar.number_input("Reorder Threshold (ROP)", min_value=0, value=int(pt_ref["rop"]))
        pt_ref["qty_per_pkg"] = st.sidebar.number_input("Qty Per Package:", min_value=1, value=int(pt_ref["qty_per_pkg"]))
        pt_ref["pkgs_per_trip"] = st.sidebar.number_input("Packages Per Trip:", min_value=1, value=int(pt_ref["pkgs_per_trip"]))

elif mod_action == "Add New Station/Drop Point":
    st.markdown("### ➕ Register New Station Node")
    new_ws_name = st.sidebar.text_input("Workstation Name (e.g. RA180):", "RA180")
    new_sub_name = st.sidebar.text_input("Sub-Station Drop Point Identifier:", "Point A")
    new_lane = st.sidebar.selectbox("Track Layout Lane Position:", ["top", "bottom"])
    new_seq = st.sidebar.number_input("Production Consumed Sequence Position:", min_value=1, value=5)
    new_dist = st.sidebar.number_input("Route Distance from Hub (Meters):", min_value=10, max_value=990, value=500)
    
    new_stock = st.sidebar.number_input("Initial Live Stock:", min_value=0, value=24)
    new_rop = st.sidebar.number_input("Reorder Boundary Point (ROP):", min_value=0, value=12)
    new_qty_pkg = st.sidebar.number_input("Box Capacity Size (Qty/Pkg):", min_value=1, value=12)
    new_pkg_trip = st.sidebar.number_input("Boxes Cargo per Move (Pkg/Trip):", min_value=1, value=1)
    
    if st.sidebar.button("💾 Apply & Inject Node to Map"):
        if new_ws_name not in st.session_state.workstations:
            st.session_state.workstations[new_ws_name] = {"lane": new_lane, "sequence_order": new_seq, "sub_stations": {}}
        
        st.session_state.workstations[new_ws_name]["sub_stations"][new_sub_name] = {
            "inventory": new_stock, "rop": new_rop, "qty_per_pkg": new_qty_pkg, "pkgs_per_trip": new_pkg_trip, "distance_meters": new_dist
        }
        st.session_state.starvation_events[f"{new_ws_name}_{new_sub_name}"] = 0
        st.success(f"Successfully integrated {new_ws_name} into floor loop matrix.")
        st.rerun()

elif mod_action == "Remove Existing Node":
    st.markdown("### ❌ Extract Node from Matrix")
    flat_subs = [f"{ws_k} -> {sub_k}" for ws_k, ws_v in st.session_state.workstations.items() for sub_k in ws_v["sub_stations"].keys()]
    target_to_delete = st.sidebar.selectbox("Select Station Drop to Remove:", flat_subs if flat_subs else ["None"])
    
    if st.sidebar.button("🗑️ Delete Selected Node Permanently", type="primary"):
        if target_to_delete and target_to_delete != "None":
            del_ws, del_sub = target_to_delete.split(" -> ")
            if del_ws in st.session_state.workstations:
                if del_sub in st.session_state.workstations[del_ws]["sub_stations"]:
                    del st.session_state.workstations[del_ws]["sub_stations"][del_sub]
                if not st.session_state.workstations[del_ws]["sub_stations"]:
                    del st.session_state.workstations[del_ws]
            st.warning(f"Extracted {target_to_delete} from logistics loop tracking path.")
            st.rerun()

st.sidebar.markdown("---")
st.sidebar.header("⚡ Simulation Engine Speed")
speed_acceleration = st.sidebar.slider("Speed Steps (seconds/frame)", min_value=1, max_value=60, value=15)

# --- 4. ENGINE RUNTIME LOGIC (WITH 480-MIN HARD CEILING LIMIT) ---
def advance_simulation(seconds):
    total_loop_meters = 1000.0
    
    for _ in range(int(seconds)):
        # Check boundary condition before adding seconds
        if st.session_state.sim_time >= SHIFT_MAX_SECONDS:
            st.session_state.running = False
            break
            
        st.session_state.sim_time += 1
        
        # A. Production Stock Depletion
        for ws_name, ws_data in list(st.session_state.workstations.items()):
            stagger_offset = (ws_data["sequence_order"] - 1) * master_takt_secs
            target_trigger_time = st.session_state.sim_time - stagger_offset
            
            if target_trigger_time > 0 and target_trigger_time % master_takt_secs == 0:
                for sub_name, sub_data in ws_data["sub_stations"].items():
                    unique_key = f"{ws_name}_{sub_name}"
                    if sub_data["inventory"] > 0:
                        sub_data["inventory"] -= 1
                    else:
                        st.session_state.starvation_events[unique_key] += 1

        # B. Loop-Driven Physics Movement Logic
        if st.session_state.tugger_status == "Idle at Start Hub":
            st.session_state.tugger_pct = 0.0
            highest_urgency = -9999
            chosen_ws, chosen_sub = None, None
            
            for ws_name, ws_data in st.session_state.workstations.items():
                for sub_name, sub_data in ws_data["sub_stations"].items():
                    if sub_data["inventory"] <= sub_data["rop"]:
                        urgency = sub_data["rop"] - sub_data["inventory"]
                        if urgency > highest_urgency:
                            highest_urgency = urgency
                            chosen_sub = sub_name
                            chosen_ws = ws_name
            
            if chosen_sub:
                st.session_state.current_target_point = (chosen_ws, chosen_sub)
                p_data = st.session_state.workstations[chosen_ws]["sub_stations"][chosen_sub]
                st.session_state.active_delivery_qty = p_data["qty_per_pkg"] * p_data["pkgs_per_trip"]
                
                st.session_state.tugger_status = f"Staging Cargo for {chosen_ws}"
                st.session_state.trip_start_time = st.session_state.sim_time
                st.session_state.process_timer = 15  
                st.session_state.max_transit_secs = 15
                
        elif st.session_state.tugger_status.startswith("Staging Cargo"):
            st.session_state.process_timer -= 1
            if st.session_state.process_timer <= 0:
                w, s = st.session_state.current_target_point
                if w in st.session_state.workstations and s in st.session_state.workstations[w]["sub_stations"]:
                    target_distance = st.session_state.workstations[w]["sub_stations"][s]["distance_meters"]
                else:
                    st.session_state.tugger_status = "Idle at Start Hub"
                    continue
                
                calc_transit_seconds = int(target_distance / speed_ms)
                st.session_state.tugger_status = f"Moving to {w}"
                st.session_state.process_timer = max(1, calc_transit_seconds)  
                st.session_state.max_transit_secs = max(1, calc_transit_seconds)
                
        elif st.session_state.tugger_status.startswith("Moving to"):
            st.session_state.process_timer -= 1
            w, s = st.session_state.current_target_point
            if w in st.session_state.workstations and s in st.session_state.workstations[w]["sub_stations"]:
                target_distance = st.session_state.workstations[w]["sub_stations"][s]["distance_meters"]
            else:
                st.session_state.tugger_status = "Idle at Start Hub"
                continue
            
            elapsed = st.session_state.max_transit_secs - st.session_state.process_timer
            ratio = elapsed / st.session_state.max_transit_secs
            
            current_meters = ratio * target_distance
            st.session_state.tugger_pct = (current_meters / total_loop_meters) * 100.0
            
            if st.session_state.process_timer <= 0:
                st.session_state.tugger_pct = (target_distance / total_loop_meters) * 100.0  
                st.session_state.tugger_status = f"Dropping at {w}"
                st.session_state.process_timer = 20  
                st.session_state.max_transit_secs = 20
                
        elif st.session_state.tugger_status.startswith("Dropping at"):
            st.session_state.process_timer -= 1
            if st.session_state.process_timer <= 0:
                w, s = st.session_state.current_target_point
                if w in st.session_state.workstations and s in st.session_state.workstations[w]["sub_stations"]:
                    st.session_state.workstations[w]["sub_stations"][s]["inventory"] += st.session_state.active_delivery_qty
                    target_distance = st.session_state.workstations[w]["sub_stations"][s]["distance_meters"]
                else:
                    st.session_state.tugger_status = "Idle at Start Hub"
                    continue
                
                st.session_state.trip_counter += 1
                remaining_return_distance = total_loop_meters - target_distance
                calc_return_seconds = int(remaining_return_distance / speed_ms)
                
                st.session_state.tugger_status = "Completing Return Loop"
                st.session_state.process_timer = max(1, calc_return_seconds)
                st.session_state.max_transit_secs = max(1, calc_return_seconds)
                
        elif st.session_state.tugger_status == "Completing Return Loop":
            st.session_state.process_timer -= 1
            w, s = st.session_state.current_target_point
            if w in st.session_state.workstations and s in st.session_state.workstations[w]["sub_stations"]:
                target_distance = st.session_state.workstations[w]["sub_stations"][s]["distance_meters"]
            else:
                st.session_state.tugger_status = "Idle at Start Hub"
                continue
            
            elapsed = st.session_state.max_transit_secs - st.session_state.process_timer
            ratio = elapsed / st.session_state.max_transit_secs
            
            current_meters = target_distance + (ratio * (total_loop_meters - target_distance))
            st.session_state.tugger_pct = (current_meters / total_loop_meters) * 100.0
            
            if st.session_state.process_timer <= 0:
                duration_secs = st.session_state.sim_time - st.session_state.trip_start_time
                st.session_state.trip_log.append({
                    "Trip ID": f"TRP-{st.session_state.trip_counter:03d}",
                    "Target Node": w,
                    "Distance (m)": f"{int(target_distance)}m",
                    "Load Quantity": f"{st.session_state.active_delivery_qty} u",
                    "Total Cycle Duration": format_to_mmss(duration_secs)
                })
                st.session_state.tugger_status = "Idle at Start Hub"
                st.session_state.current_target_point = None
                st.session_state.tugger_pct = 0.0

    snap = {}
    for ws_k, ws_v in st.session_state.workstations.items():
        for sub_k, d in ws_v["sub_stations"].items():
            snap[f"{ws_k}_{sub_k}"] = d["inventory"]
    if snap:
        st.session_state.chart_data = pd.concat([st.session_state.chart_data, pd.DataFrame([snap])], ignore_index=True)

# --- 5. SYSTEM COMMANDS TOOLBAR ---
c1, c2, c3 = st.columns(3)
with c1:
    is_shift_done = (st.session_state.sim_time >= SHIFT_MAX_SECONDS)
    if st.button("▶️ Start Simulation Run", use_container_width=True, disabled=is_shift_done):
        st.session_state.running = True
with c2:
    if st.button("⏸️ Pause Engine", use_container_width=True):
        st.session_state.running = False
with c3:
    if st.button("🔄 Clear & Reset State", use_container_width=True):
        st.session_state.sim_time = 0
        st.session_state.trip_log = []
        st.session_state.tugger_status = "Idle at Start Hub"
        st.session_state.current_target_point = None
        st.session_state.running = False
        st.session_state.tugger_pct = 0.0
        for p in list(st.session_state.starvation_events.keys()):
            st.session_state.starvation_events[p] = 0
        st.rerun()

# --- 6. USER INTERFACE PAGES ---
if app_mode == "🗺️ Live Simulation Map":
    st.title("🗺️ Factory Circular Flow Tracker")
    
    # Progress tracker bar for shift runtime visualizer
    shift_progress_ratio = min(1.0, float(st.session_state.sim_time) / SHIFT_MAX_SECONDS)
    st.progress(shift_progress_ratio, text=f"⏱️ Shift Execution Timeline Status: {format_to_mmss(st.session_state.sim_time)} / 480:00 Mins")
    
    if is_shift_done:
        st.success("🏁 **Shift Execution Finished:** Target window limit of 480 Minutes achieved. Engine locked.")
        
    map_container_box = st.empty()
    status_msg_box = st.empty()
    kpi_metric_row = st.empty()

    def generate_html_floorplan():
        pct = st.session_state.tugger_pct
        tgt_tuple = st.session_state.current_target_point
        tgt_ws = tgt_tuple[0] if tgt_tuple else None
        
        station_markers = ""
        info_cards = ""
        
        for ws_name, ws_data in st.session_state.workstations.items():
            for sub_name, d in ws_data["sub_stations"].items():
                m_range = d["distance_meters"]
                is_active_target = (ws_name == tgt_ws)
                is_targeted = "background: #fa5252; color: white; border: 2px solid #fff; box-shadow: 0 0 12px #fa5252; transform: translate(-50%, -50%) scale(1.05);" if is_active_target else "background: #343a40; color: #f8f9fa; border: 1px solid #495057; transform: translate(-50%, -50%);"
                unique_key = f"{ws_name}_{sub_name}"
                
                if ws_data["lane"] == "top":
                    x_pos = 20.0 + ((m_range - 100.0) / 400.0) * 65.0
                    y_pos = 20.0
                else:
                    x_pos = 85.0 - ((m_range - 550.0) / 350.0) * 65.0
                    y_pos = 80.0

                station_markers += f"""
                <div class="station-node-pin" style="left: {x_pos}%; top: {y_pos}%; {is_targeted}">
                    <div style="font-weight: bold; font-size: 11px;">{ws_name}</div>
                    <div style="font-size: 9px; opacity: 0.8;">{int(m_range)}m</div>
                    <div style="font-size: 8px; color: #5c7cfa;">Seq #{ws_data['sequence_order']}</div>
                </div>
                """
                
                card_style = "border-top: 4px solid #fa5252; background-color: #fff5f5;" if is_active_target else "border-top: 4px solid #1c7ed6;"
                shortage_val = st.session_state.starvation_events.get(unique_key, 0)
                info_cards += f"""
                <div class="kpi-card-block" style="{card_style}">
                    <div class="card-title">🏭 {ws_name} <span style="font-size:10px; color:#6c757d;">({int(m_range)}m)</span></div>
                    <div class="card-stock">📦 {d['inventory']} <span style="font-size:11px; color:#495057;">u</span></div>
                    <div class="card-meta">
                        Pack size: <b>{d['qty_per_pkg']}u x {d['pkgs_per_trip']}</b><br>
                        ROP: <b>{d['rop']} u</b> | Seq: <b>#{ws_data['sequence_order']}</b><br>
                        Shortage: <span style="color:#fa5252; font-weight:bold;">{format_to_mmss(shortage_val)}</span>
                    </div>
                </div>
                """
                
        car_html = ""
        if pct > 0:
            if pct <= 50.0:
                cx = 5.0 + (pct / 50.0) * 80.0
                cy = 20.0
                lbl = "🚜 Transit"
                c_class = ""
            else:
                cx = 85.0 - ((pct - 50.0) / 50.0) * 80.0
                cy = 80.0
                lbl = "🚜 Return"
                c_class = "returning"
            car_html = f'<div class="tugger-truck {c_class}" style="left: {cx}%; top: {cy}%; transform: translate(-50%, -50%);">{lbl}</div>'

        return f"""
        <style>
            .floorplan-wrapper {{ background: #f8f9fa; border: 1px solid #e9ecef; border-radius: 12px; padding: 20px; font-family: system-ui, sans-serif; }}
            .cards-outer-container {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); gap: 10px; margin-top: 20px; }}
            .kpi-card-block {{ background: #ffffff; border: 1px solid #dee2e6; border-radius: 6px; padding: 10px; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.01); }}
            .card-title {{ font-weight: bold; font-size: 13px; color: #212529; }}
            .card-stock {{ font-size: 18px; font-weight: 800; color: #1c7ed6; margin: 2px 0; }}
            .card-meta {{ font-size: 10.5px; color: #6c757d; border-top: 1px solid #f1f3f5; padding-top: 4px; margin-top: 4px; line-height: 1.4; }}
            .loop-track-container {{ height: 140px; border: 4px solid #e03131; border-radius: 70px; position: relative; margin: 20px 10px; background: #ffffff; }}
            .hub-terminal {{ position: absolute; left: 5%; top: 50%; transform: translate(-50%, -50%); background: #1c7ed6; color: white; padding: 6px 12px; border-radius: 20px; font-size: 11px; font-weight: bold; z-index: 16; text-align: center; line-height: 1.2; box-shadow: 0 3px 6px rgba(0,0,0,0.1); }}
            .station-node-pin {{ position: absolute; padding: 4px 8px; border-radius: 4px; z-index: 12; text-align: center; min-width: 60px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); transition: all 0.1s linear; }}
            .tugger-truck {{ position: absolute; background: #2b8a3e; color: white; padding: 4px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; z-index: 20; white-space: nowrap; border: 1px solid #fff; box-shadow: 0 3px 8px rgba(43,138,62,0.3); }}
            .tugger-truck.returning {{ background: #e03131; box-shadow: 0 3px 8px rgba(224,49,49,0.3); }}
        </style>
        <div class="floorplan-wrapper">
            <div class="loop-track-container">
                <div class="hub-terminal">🏁 START /<br>RETURN</div>
                {station_markers}
                {car_html}
            </div>
            <div class="cards-outer-container">{info_cards}</div>
        </div>
        """

    if st.session_state.running:
        while st.session_state.running:
            advance_simulation(speed_acceleration)
            map_container_box.html(generate_html_floorplan())
            
            status_msg_box.info(f"🚜 **Tugger Fleet Dispatch Status:** `{st.session_state.tugger_status}` (Remaining: `{st.session_state.process_timer}s` | Active Track Coordinate: `{int(st.session_state.tugger_pct * 10)}m`)")
            
            with kpi_metric_row.container():
                m1, m2, m3 = st.columns(3)
                m1.metric("⏱️ Simulation Runtime Elapsed", format_to_mmss(st.session_state.sim_time))
                m2.metric("🚜 Completed Loop Runs", f"{st.session_state.trip_counter} Cycles")
                m3.metric("📦 Car Active Payload Volume", f"{st.session_state.active_delivery_qty} units")
            
            time.sleep(0.04)
            if st.session_state.sim_time >= SHIFT_MAX_SECONDS:
                st.rerun()
    else:
        map_container_box.html(generate_html_floorplan())
        if is_shift_done:
            status_msg_box.success("🏁 Shift cycle fully executed (480 Mins Complete).")
        else:
            status_msg_box.info(f"⏸️ **Simulation Paused:** `{st.session_state.tugger_status}`")

# --- 7. ANALYSIS PAGE WITH LIVE TUGGER ROUND ANALYSIS ---
elif app_mode == "📊 Lineside Stock & Refill Analysis":
    st.title("📊 Lineside Stock & Tugger Logistics Performance")
    
    # NEW BLOCK: TUGGER SPEED / DISPATCH TIME ANALYTICS SECTION
    st.header("🚜 Logistics Tugger Round Analytics")
    
    if not st.session_state.trip_log:
        st.info("No logistics cycles logged yet. Run simulation to review trip duration performance metrics.")
    else:
        # Calculate real mathematical metrics across logged trips
        df_trips = pd.DataFrame(st.session_state.trip_log)
        
        # Convert MM:SS loop duration records back into absolute seconds to get accurate performance stats
        df_trips["duration_secs"] = df_trips["Total Cycle Duration"].apply(parse_mmss_to_seconds)
        
        avg_secs = df_trips["duration_secs"].mean()
        min_secs = df_trips["duration_secs"].min()
        max_secs = df_trips["duration_secs"].max()
        
        t_col1, t_col2, t_col3, t_col4 = st.columns(4)
        t_col1.metric("⏱️ Avg Round Duration", format_to_mmss(avg_secs))
        t_col2.metric("⚡ Fastest Round Sprint", format_to_mmss(min_secs))
        t_col3.metric("🐌 Longest Round Transit", format_to_mmss(max_secs))
        t_col4.metric("📈 Total Round Dispatches", f"{len(df_trips)} Runs")
        
        # Mini bar breakdown visualization of the logistics workload distribution
        with st.container(border=True):
            st.markdown("**📊 Loop Duration Spectrum by Target Workstation Location**")
            chart_data_trips = df_trips[["Target Node", "duration_secs"]].copy()
            chart_data_trips = chart_data_trips.rename(columns={"duration_secs": "Round Time Duration (Seconds)"})
            st.bar_chart(chart_data_trips, x="Target Node", y="Round Time Duration (Seconds)", height=200)

    st.markdown("---")
    st.header("📉 Individual Sub-Station Shelf Buffer Profiles")
    
    active_cols = [c for c in st.session_state.chart_data.columns if "_" in str(c)]
    
    if not active_cols:
        st.warning("No drop metrics tracked yet.")
    else:
        for col_name in active_cols:
            try:
                ws_part, sub_part = str(col_name).split("_")
            except ValueError:
                continue 
                
            ws_d = st.session_state.workstations.get(ws_part, {})
            if not ws_d or sub_part not in ws_d["sub_stations"]:
                continue
                
            pt_data = ws_d["sub_stations"][sub_part]
            current_stock = pt_data["inventory"]
            rop_value = pt_data["rop"]
            refill_qty = pt_data["qty_per_pkg"] * pt_data["pkgs_per_trip"]
            
            if current_stock == 0:
                status_label = "🚨 STARVED (Line Stopped)"
                color_theme = "red"
            elif current_stock <= rop_value:
                status_label = "⚠️ BELOW REORDER POINT (Refill Triggered)"
                color_theme = "orange"
            else:
                status_label = "✅ HEALTHY BUFFER"
                color_theme = "green"
                
            with st.container(border=True):
                col_left, col_right = st.columns([2, 5])
                
                with col_left:
                    st.subheader(f"📍 Station {ws_part}")
                    st.markdown(f"Status: :{color_theme}[**{status_label}**]")
                    
                    max_capacity_est = max(30, rop_value + refill_qty)
                    progress_pct = min(1.0, float(current_stock) / max_capacity_est)
                    st.progress(progress_pct, text=f"Shelf Load Level: {current_stock} / {max_capacity_est} Units")
                    
                    m1, m2 = st.columns(2)
                    m1.metric("📉 Reorder Point (ROP)", f"{rop_value} u")
                    m2.metric("🚛 Refill Drop Volume", f"+{refill_qty} u")
                    
                    starve_time = st.session_state.starvation_events.get(col_name, 0)
                    st.caption(f"⏱️ Accumulative Starvation Time: **{format_to_mmss(starve_time)}**")
                    st.caption(f"🏁 Route Loop Index: **{int(pt_data['distance_meters'])}m** | Sequence: **#{ws_d['sequence_order']}**")
                
                with col_right:
                    chart_slice = st.session_state.chart_data[col_name].iloc[-300:]
                    st.line_chart(chart_slice, height=180)
        
        st.markdown("---")
        st.subheader("📋 Historical Trip Logs Ledger")
        st.dataframe(pd.DataFrame(st.session_state.trip_log), use_container_width=True)
