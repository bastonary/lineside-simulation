import streamlit as st
import pandas as pd
import numpy as np
import time

st.set_page_config(layout="wide")

# --- 1. CLOCK FORMATTING HELPER ---
def format_to_mmss(total_seconds):
    minutes = int(total_seconds) // 60
    seconds = int(total_seconds) % 60
    return f"{minutes:02d}:{seconds:02d}"

# --- 2. INITIALIZE GLOBAL STATE WITH OVAL LOOP GEOMETRY ---
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

    # Progressive distance metrics over a 1000m continuous loop circuit
    # Top track: 0m -> 500m (Left to Right) | Bottom track: 500m -> 1000m (Right to Left)
    st.session_state.workstations = {
        "RA140": {"lane": "top",    "sequence_order": 1, "sub_stations": {"Point A": {"inventory": 22, "rop": 10, "qty_per_pkg": 8,  "pkgs_per_trip": 4, "distance_meters": 150}}},
        "RA130": {"lane": "top",    "sequence_order": 2, "sub_stations": {"Point A": {"inventory": 25, "rop": 12, "qty_per_pkg": 10, "pkgs_per_trip": 4, "distance_meters": 250}}},
        "RA120": {"lane": "top",    "sequence_order": 3, "sub_stations": {"Point A": {"inventory": 23, "rop": 12, "qty_per_pkg": 12, "pkgs_per_trip": 3, "distance_meters": 350}}},
        "RA110": {"lane": "top",    "sequence_order": 4, "sub_stations": {"Point A": {"inventory": 24, "rop": 12, "qty_per_pkg": 10, "pkgs_per_trip": 3, "distance_meters": 450}}},
        "RA170": {"lane": "bottom", "sequence_order": 5, "sub_stations": {"Point A": {"inventory": 25, "rop": 15, "qty_per_pkg": 10, "pkgs_per_trip": 4, "distance_meters": 600}}},
        "RA160": {"lane": "bottom", "sequence_order": 6, "sub_stations": {"Point A": {"inventory": 26, "rop": 12, "qty_per_pkg": 12, "pkgs_per_trip": 3, "distance_meters": 720}}},
        "RA150": {"lane": "bottom", "sequence_order": 7, "sub_stations": {"Point A": {"inventory": 24, "rop": 12, "qty_per_pkg": 10, "pkgs_per_trip": 3, "distance_meters": 840}}}
    }
    
    for ws_name, ws_data in st.session_state.workstations.items():
        for sub_name in ws_data["sub_stations"].keys():
            unique_key = f"{ws_name}_{sub_name}"
            st.session_state.starvation_events[unique_key] = 0

    snap = {f"{ws}_{sub}": d["inventory"] for ws in st.session_state.workstations.values() for sub, d in ws["sub_stations"].items()}
    st.session_state.chart_data = pd.DataFrame([snap])

# --- 3. SIDEBAR CONTROLS ---
st.sidebar.title("🎮 Factory Control Room")
app_mode = st.sidebar.selectbox("📂 Select Dashboard Page", ["🗺️ Live Simulation Map", "📊 Isolated Shortage Analytics"])

st.sidebar.header("⚙️ Master Line Rate Settings")
master_takt_mins = st.sidebar.number_input("Whole Line Master Takt (Minutes)", min_value=1.0, value=8.0, step=0.5)
master_takt_secs = int(master_takt_mins * 60)

st.sidebar.header("🚜 Logistics Towing Properties")
speed_kmh = st.sidebar.number_input("Tugger Travel Speed (km/h)", min_value=1.0, max_value=30.0, value=12.0, step=0.5)
speed_ms = (speed_kmh * 1000.0) / 3600.0

st.sidebar.header("🏢 Modify Station Parameters")
flat_subs = [f"{ws_k} -> {sub_k}" for ws_k, ws_v in st.session_state.workstations.items() for sub_k in ws_v["sub_stations"].keys()]
selected_flat = st.sidebar.selectbox("Select Node to Modify:", flat_subs if flat_subs else ["None"])

if selected_flat and selected_flat != "None":
    ws_target, sub_target = selected_flat.split(" -> ")
    pt_ref = st.session_state.workstations[ws_target]["sub_stations"][sub_target]
    
    st.markdown(f"⚙️ **Editing:** `{ws_target}`")
    pt_ref["distance_meters"] = st.sidebar.number_input("Meter Distance from Start Hub:", min_value=10, max_value=990, value=int(pt_ref["distance_meters"]))
    pt_ref["inventory"] = st.sidebar.number_input("Live Stock Level (Units)", min_value=0, value=int(pt_ref["inventory"]))
    pt_ref["rop"] = st.sidebar.number_input("Reorder Threshold (ROP)", min_value=0, value=int(pt_ref["rop"]))

st.sidebar.markdown("---")
st.sidebar.header("⚡ Simulation Engine Speed")
speed_acceleration = st.sidebar.slider("Speed Steps (seconds/frame)", min_value=1, max_value=60, value=15)

# --- 4. ENGINE RUNTIME LOGIC ---
def advance_simulation(seconds):
    total_loop_meters = 1000.0
    
    for _ in range(int(seconds)):
        st.session_state.sim_time += 1
        
        # A. Production Stock Depletion
        for ws_name, ws_data in st.session_state.workstations.items():
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
                target_distance = st.session_state.workstations[w]["sub_stations"][s]["distance_meters"]
                
                calc_transit_seconds = int(target_distance / speed_ms)
                st.session_state.tugger_status = f"Moving to {w}"
                st.session_state.process_timer = max(1, calc_transit_seconds)  
                st.session_state.max_transit_secs = max(1, calc_transit_seconds)
                
        elif st.session_state.tugger_status.startswith("Moving to"):
            st.session_state.process_timer -= 1
            w, s = st.session_state.current_target_point
            target_distance = st.session_state.workstations[w]["sub_stations"][s]["distance_meters"]
            
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
                
                st.session_state.trip_counter += 1
                target_distance = st.session_state.workstations[w]["sub_stations"][s]["distance_meters"]
                
                remaining_return_distance = total_loop_meters - target_distance
                calc_return_seconds = int(remaining_return_distance / speed_ms)
                
                st.session_state.tugger_status = "Completing Return Loop"
                st.session_state.process_timer = max(1, calc_return_seconds)
                st.session_state.max_transit_secs = max(1, calc_return_seconds)
                
        elif st.session_state.tugger_status == "Completing Return Loop":
            st.session_state.process_timer -= 1
            w, s = st.session_state.current_target_point
            target_distance = st.session_state.workstations[w]["sub_stations"][s]["distance_meters"]
            
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

    snap = {f"{ws_k}_{sub_k}": d["inventory"] for ws_k, ws_v in st.session_state.workstations.items() for sub_k, d in ws_v["sub_stations"].items()}
    st.session_state.chart_data = pd.concat([st.session_state.chart_data, pd.DataFrame([snap])], ignore_index=True)

# --- 5. SYSTEM COMMANDS TOOLBAR ---
c1, c2, c3 = st.columns(3)
with c1:
    if st.button("▶️ Start Simulation Run", use_container_width=True):
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
        for p in st.session_state.starvation_events.keys():
            st.session_state.starvation_events[p] = 0
        st.rerun()

# --- 6. USER INTERFACE PAGE RENDER ---
if app_mode == "🗺️ Live Simulation Map":
    st.title("🗺️ Factory Circular Flow Tracker")
    
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
                
                # Top lane goes Left -> Right. Bottom lane goes Right -> Left.
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
                </div>
                """
                
                card_style = "border-top: 4px solid #fa5252; background-color: #fff5f5;" if is_active_target else "border-top: 4px solid #1c7ed6;"
                info_cards += f"""
                <div class="kpi-card-block" style="{card_style}">
                    <div class="card-title">🏭 {ws_name} <span style="font-size:10px; color:#6c757d;">({int(m_range)}m)</span></div>
                    <div class="card-stock">📦 {d['inventory']} <span style="font-size:11px; color:#495057;">u</span></div>
                    <div class="card-meta">
                        ROP: <b>{d['rop']} u</b> <br>
                        Shortage: <span style="color:#fa5252; font-weight:bold;">{format_to_mmss(st.session_state.starvation_events[unique_key])}</span>
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
            .cards-outer-container {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; margin-top: 20px; }}
            .kpi-card-block {{ background: #ffffff; border: 1px solid #dee2e6; border-radius: 6px; padding: 10px; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.01); }}
            .card-title {{ font-weight: bold; font-size: 13px; color: #212529; }}
            .card-stock {{ font-size: 18px; font-weight: 800; color: #1c7ed6; margin: 2px 0; }}
            .card-meta {{ font-size: 11px; color: #6c757d; border-top: 1px solid #f1f3f5; padding-top: 4px; margin-top: 4px; line-height: 1.3; }}
            
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
    else:
        map_container_box.html(generate_html_floorplan())
        status_msg_box.info(f"⏸️ **Simulation Paused:** `{st.session_state.tugger_status}`")

# --- 7. SANITIZED BOTTLENECK SHORTAGE ANALYTICS PAGE ---
elif app_mode == "📊 Isolated Shortage Analytics":
    st.title("📊 Sub-Station Isolated Bottleneck Analysis Dashboard")
    
    # Filter for active simulation station tracking columns
    active_cols = [c for c in st.session_state.chart_data.columns if "_" in str(c)]
    
    if not active_cols:
        st.warning("No drop sub-station locations logged.")
    else:
        for col_name in active_cols:
            # Safe parsing guard to prevent crashing if a column formatting anomaly occurs
            try:
                ws_part, sub_part = str(col_name).split("_")
            except ValueError:
                continue 
                
            with st.container(border=True):
                c_left, c_right = st.columns([1, 4])
                with c_left:
                    st.subheader(f"📍 {ws_part}")
                    st.caption(f"Sub-Station: **{sub_part}**")
                    
                    shortage_duration_secs = st.session_state.starvation_events.get(col_name, 0)
                    st.metric("🚨 Total Starvation Time", format_to_mmss(shortage_duration_secs))
                    
                    ws_d = st.session_state.workstations.get(ws_part, {})
                    if ws_d:
                        st.caption(f"**Loop Range Location:** {int(ws_d['sub_stations'][sub_part]['distance_meters'])} meters")
                with c_right:
                    st.line_chart(st.session_state.chart_data[col_name].iloc[-400:], height=150)
        
        st.markdown("---")
        st.subheader("📋 Historical Trip Logs Ledger")
        if not st.session_state.trip_log:
            st.info("No distribution cycles completed yet.")
        else:
            st.dataframe(pd.DataFrame(st.session_state.trip_log), use_container_width=True)
