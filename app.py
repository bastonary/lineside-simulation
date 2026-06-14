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

# --- 2. INITIALIZE ARTIFACT GLOBAL STATE ---
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

    # Track distances precisely sequential along a single 0-100% continuous line loop
    # Top lane runs left-to-right (0% -> 50%). Bottom lane runs right-to-left (50% -> 100%).
    st.session_state.workstations = {
        "RA140": {"lane": "top",    "sequence_order": 1, "sub_stations": {"Point A": {"inventory": 22, "rop": 10, "qty_per_pkg": 8,  "pkgs_per_trip": 4, "distance_pct": 10}}},
        "RA130": {"lane": "top",    "sequence_order": 2, "sub_stations": {"Point A": {"inventory": 25, "rop": 12, "qty_per_pkg": 10, "pkgs_per_trip": 4, "distance_pct": 22}}},
        "RA120": {"lane": "top",    "sequence_order": 3, "sub_stations": {"Point A": {"inventory": 23, "rop": 12, "qty_per_pkg": 12, "pkgs_per_trip": 3, "distance_pct": 34}}},
        "RA110": {"lane": "top",    "sequence_order": 4, "sub_stations": {"Point A": {"inventory": 24, "rop": 12, "qty_per_pkg": 10, "pkgs_per_trip": 3, "distance_pct": 45}}},
        "RA150": {"lane": "bottom", "sequence_order": 5, "sub_stations": {"Point A": {"inventory": 24, "rop": 12, "qty_per_pkg": 10, "pkgs_per_trip": 3, "distance_pct": 60}}},
        "RA160": {"lane": "bottom", "sequence_order": 6, "sub_stations": {"Point A": {"inventory": 26, "rop": 12, "qty_per_pkg": 12, "pkgs_per_trip": 3, "distance_pct": 74}}},
        "RA170": {"lane": "bottom", "sequence_order": 7, "sub_stations": {"Point A": {"inventory": 25, "rop": 15, "qty_per_pkg": 10, "pkgs_per_trip": 4, "distance_pct": 88}}}
    }
    
    for ws_name, ws_data in st.session_state.workstations.items():
        for sub_name in ws_data["sub_stations"].keys():
            unique_key = f"{ws_name}_{sub_name}"
            st.session_state.starvation_events[unique_key] = 0

    snap = {f"{ws}_{sub}": d["inventory"] for ws in st.session_state.workstations.values() for sub, d in ws["sub_stations"].items()}
    st.session_state.chart_data = pd.DataFrame([snap])

# --- 3. SIDEBAR ENGINE CONTROL PANEL ---
st.sidebar.title("🎮 Factory Control Room")
app_mode = st.sidebar.selectbox("📂 Select Dashboard Page", ["🗺️ Live Simulation Map", "📊 Isolated Shortage Analytics"])

st.sidebar.header("⚙️ Master Line Rate Settings")
master_takt_mins = st.sidebar.number_input("Whole Line Master Takt (Minutes)", min_value=1.0, value=8.0, step=0.5)
master_takt_secs = int(master_takt_mins * 60)

st.sidebar.header("🏢 Modify Station Parameters")
flat_subs = [f"{ws_k} -> {sub_k}" for ws_k, ws_v in st.session_state.workstations.items() for sub_k in ws_v["sub_stations"].keys()]
selected_flat = st.sidebar.selectbox("Select Node to Modify:", flat_subs if flat_subs else ["None"])

if selected_flat and selected_flat != "None":
    ws_target, sub_target = selected_flat.split(" -> ")
    pt_ref = st.session_state.workstations[ws_target]["sub_stations"][sub_target]
    
    st.markdown(f"⚙️ **Editing:** `{ws_target}`")
    st.session_state.workstations[ws_target]["sequence_order"] = st.sidebar.number_input("Sequence Order Position:", min_value=1, value=int(st.session_state.workstations[ws_target]["sequence_order"]))
    pt_ref["inventory"] = st.sidebar.number_input("Live Stock Level (Units)", min_value=0, value=int(pt_ref["inventory"]))
    pt_ref["rop"] = st.sidebar.number_input("Reorder Threshold (ROP)", min_value=0, value=int(pt_ref["rop"]))
    pt_ref["qty_per_pkg"] = st.sidebar.number_input("Units Count Per Box", min_value=1, value=int(pt_ref["qty_per_pkg"]))
    pt_ref["pkgs_per_trip"] = st.sidebar.number_input("Boxes Dispatched per Trip", min_value=1, value=int(pt_ref["pkgs_per_trip"]))

st.sidebar.markdown("---")
st.sidebar.header("⚡ Simulation Processing Engine")
speed_acceleration = st.sidebar.slider("Simulation Processing Speed Steps (s)", min_value=1, max_value=60, value=15)

# --- 4. ENGINE ADVANCEMENT PARSING PROCESS ---
def advance_simulation(seconds):
    for _ in range(int(seconds)):
        st.session_state.sim_time += 1
        
        # A. Takt Rate Processing Matrix
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

        # B. Single-Car One-Way Loop Dispatch System
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
                st.session_state.tugger_status = f"Loading Car at Start Hub for {chosen_ws}"
                st.session_state.trip_start_time = st.session_state.sim_time
                st.session_state.process_timer = 20  
                st.session_state.max_transit_secs = 20
                
        elif st.session_state.tugger_status.startswith("Loading Car"):
            st.session_state.process_timer -= 1
            if st.session_state.process_timer <= 0:
                w, s = st.session_state.current_target_point
                st.session_state.tugger_status = f"One-Way Run to {w}"
                st.session_state.process_timer = 45  
                st.session_state.max_transit_secs = 45
                
        elif st.session_state.tugger_status.startswith("One-Way Run"):
            st.session_state.process_timer -= 1
            w, s = st.session_state.current_target_point
            max_pos = st.session_state.workstations[w]["sub_stations"][s]["distance_pct"]
            elapsed = st.session_state.max_transit_secs - st.session_state.process_timer
            st.session_state.tugger_pct = (elapsed / st.session_state.max_transit_secs) * max_pos
            
            if st.session_state.process_timer <= 0:
                st.session_state.tugger_pct = max_pos  
                st.session_state.tugger_status = f"Unloading at {w}"
                st.session_state.process_timer = 25  
                st.session_state.max_transit_secs = 25
                
        elif st.session_state.tugger_status.startswith("Unloading at"):
            st.session_state.process_timer -= 1
            if st.session_state.process_timer <= 0:
                w, s = st.session_state.current_target_point
                if w in st.session_state.workstations and s in st.session_state.workstations[w]["sub_stations"]:
                    st.session_state.workstations[w]["sub_stations"][s]["inventory"] += st.session_state.active_delivery_qty
                
                st.session_state.trip_counter += 1
                st.session_state.tugger_status = "Advancing to Return Point"
                st.session_state.process_timer = 45
                st.session_state.max_transit_secs = 45
                
        elif st.session_state.tugger_status == "Advancing to Return Point":
            st.session_state.process_timer -= 1
            w, s = st.session_state.current_target_point
            max_pos = st.session_state.workstations[w]["sub_stations"][s]["distance_pct"]
            elapsed = st.session_state.max_transit_secs - st.session_state.process_timer
            ratio = elapsed / st.session_state.max_transit_secs
            
            # The single car drives FORWARD to 100% to arrive back at the left-hand Return Point terminal
            st.session_state.tugger_pct = max_pos + (ratio * (100.0 - max_pos))
            
            if st.session_state.process_timer <= 0:
                duration_secs = st.session_state.sim_time - st.session_state.trip_start_time
                st.session_state.trip_log.append({
                    "Trip ID": f"TRP-{st.session_state.trip_counter:03d}",
                    "Target Workstation": w,
                    "Delivered Volume": f"{st.session_state.active_delivery_qty} units",
                    "Total Cycle Time": format_to_mmss(duration_secs)
                })
                st.session_state.tugger_status = "Idle at Start Hub"
                st.session_state.current_target_point = None
                st.session_state.tugger_pct = 0.0

    snap = {f"{ws_k}_{sub_k}": d["inventory"] for ws_k, ws_v in st.session_state.workstations.items() for sub_k, d in ws_v["sub_stations"].items()}
    st.session_state.chart_data = pd.concat([st.session_state.chart_data, pd.DataFrame([snap])], ignore_index=True)

# --- 5. SYSTEM COMMAND TOOLBAR ---
c1, c2, c3 = st.columns(3)
with c1:
    if st.button("▶️ Run Production Cycle", use_container_width=True):
        st.session_state.running = True
with c2:
    if st.button("⏸️ Freeze Production", use_container_width=True):
        st.session_state.running = False
with c3:
    if st.button("🔄 Full Reset Plant State", use_container_width=True):
        st.session_state.sim_time = 0
        st.session_state.trip_log = []
        st.session_state.tugger_status = "Idle at Start Hub"
        st.session_state.current_target_point = None
        st.session_state.running = False
        st.session_state.tugger_pct = 0.0
        for p in st.session_state.starvation_events.keys():
            st.session_state.starvation_events[p] = 0
        st.rerun()

# --- 6. VISUAL APP RENDER EXECUTION INTERFACE ---
if app_mode == "🗺️ Live Simulation Map":
    st.title("🗺️ Factory U-Shaped Flow Tracking Map (Single Car / One-Way Run)")
    
    map_container_box = st.empty()
    status_msg_box = st.empty()
    kpi_metric_row = st.empty()

    def generate_html_floorplan():
        pct = st.session_state.tugger_pct
        tgt_tuple = st.session_state.current_target_point
        tgt_ws = tgt_tuple[0] if tgt_tuple else None
        
        top_markers = ""
        bottom_markers = ""
        info_cards = ""
        
        # Segment stations into their designated positions on the U-loop path
        for ws_name, ws_data in st.session_state.workstations.items():
            for sub_name, d in ws_data["sub_stations"].items():
                pos = d["distance_pct"]
                is_active_target = (ws_name == tgt_ws)
                is_targeted = "background: #fa5252; color: white; border: 3px solid #fff; box-shadow: 0 0 20px #fa5252; transform: translateX(-50%) scale(1.1);" if is_active_target else "background: #343a40; color: #f8f9fa; border: 1px solid #495057;"
                unique_key = f"{ws_name}_{sub_name}"
                
                # Render scaling coordinates safely according to layout orientation
                if ws_data["lane"] == "top":
                    # Map 0% -> 45% linearly across the horizontal track line length
                    horizontal_pct = (pos / 45.0) * 80.0 + 5.0
                else:
                    # Map 60% -> 88% linearly in reverse matching the right-to-left layout direction
                    horizontal_pct = 85.0 - ((pos - 60.0) / 28.0) * 75.0

                pin_html = f"""
                <div class="station-node-pin" style="left: {horizontal_pct}%; {is_targeted}">
                    <div style="font-size: 13px; font-weight: bold;">📍 {ws_name}</div>
                    <div style="font-size: 10px; opacity: 0.9;">{sub_name}</div>
                </div>
                """
                
                if ws_data["lane"] == "top":
                    top_markers += pin_html
                else:
                    bottom_markers += pin_html
                
                card_style = "border-top: 5px solid #fa5252; background-color: #fff5f5;" if is_active_target else "border-top: 5px solid #1c7ed6;"
                info_cards += f"""
                <div class="kpi-card-block" style="{card_style}">
                    <div class="card-title">🏭 {ws_name}</div>
                    <div class="card-stock">📦 {d['inventory']} <span style="font-size:12px; font-weight:normal; color:#495057;">u</span></div>
                    <div class="card-meta">
                        ROP: <b>{d['rop']} u</b> <br>
                        Shortage: <span style="color:#fa5252; font-weight:bold;">{format_to_mmss(st.session_state.starvation_events[unique_key])}</span>
                    </div>
                </div>
                """
                
        # Calculate single car coordinate position along the track circuit lines
        top_car_tag = ""
        bottom_car_tag = ""
        
        if pct > 0:
            if pct <= 50:
                # Top lane: Car driving from Left to Right
                scaled_pos = (pct / 50.0) * 85.0 + 5.0
                top_car_tag = f'<div class="tugger-truck" style="left: {scaled_pos}%;">🚜 Car 1 (Active Delivery)</div>'
            else:
                # Bottom lane: Car continuing forward from Right to Left towards Return Point
                scaled_pos = 90.0 - ((pct - 50.0) / 50.0) * 85.0
                bottom_car_tag = f'<div class="tugger-truck returning" style="left: {scaled_pos}%;">🚜 Car 1 (Returning Empty)</div>'

        return f"""
        <style>
            .floorplan-wrapper {{ background: #f8f9fa; border: 2px solid #e9ecef; border-radius: 16px; padding: 32px; font-family: system-ui, sans-serif; }}
            .cards-outer-container {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 12px; margin-top: 30px; }}
            .kpi-card-block {{ background: #ffffff; border: 1px solid #dee2e6; border-radius: 8px; padding: 12px; box-shadow: 0 4px 8px rgba(0,0,0,0.03); text-align: center; }}
            .card-title {{ font-weight: bold; font-size: 14px; color: #212529; }}
            .card-stock {{ font-size: 22px; font-weight: 800; color: #1c7ed6; margin: 4px 0; }}
            .card-meta {{ font-size: 11px; color: #6c757d; line-height: 1.4; border-top: 1px solid #f1f3f5; padding-top: 4px; margin-top: 6px; }}
            
            .runway-lane-track {{ height: 55px; background: linear-gradient(180deg, #e9ecef 0%, #ced4da 100%); border-top: 4px dashed #868e96; border-bottom: 4px dashed #868e96; position: relative; margin: 60px 0; border-radius: 6px; }}
            .lane-label {{ position: absolute; left: 140px; top: 16px; font-size: 13px; font-weight: 800; color: #adb5bd; letter-spacing: 1.5px; }}
            
            .docking-terminal {{ position: absolute; left: 0%; top: -38px; background: #2b8a3e; color: white; padding: 8px 18px; border-radius: 4px; font-size: 12px; font-weight: bold; z-index: 15; transform: translateX(-50%); box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
            .turnaround-curve {{ position: absolute; right: 2%; top: 27px; width: 50px; height: 175px; border: 5px dashed #868e96; border-left: none; border-radius: 0 50px 50px 0; z-index: 1; }}
            
            .station-node-pin {{ position: absolute; top: -38px; padding: 8px 14px; border-radius: 8px; z-index: 12; text-align: center; line-height: 1.3; box-shadow: 0 6px 12px rgba(0,0,0,0.08); transition: all 0.2s; min-width: 90px; transform: translateX(-50%); }}
            
            .tugger-truck {{ position: absolute; top: -56px; background: #1c7ed6; color: white; padding: 8px 16px; border-radius: 6px; font-size: 13px; font-weight: bold; transform: translateX(-50%); z-index: 20; white-space: nowrap; box-shadow: 0 8px 20px rgba(28,126,214,0.3); border: 2px solid #fff; }}
            .tugger-truck.returning {{ background: #e03131; box-shadow: 0 8px 20px rgba(224,49,49,0.3); }}
            .flow-arrow {{ position: absolute; right: 80px; top: 16px; font-size: 16px; color: #adb5bd; font-weight: bold; }}
        </style>
        <div class="floorplan-wrapper">
            <div style="position: relative; margin: 20px 10px;">
                
                <div class="turnaround-curve"></div>

                <div class="runway-lane-track" style="width: 95%;">
                    <div class="docking-terminal" style="background:#2b8a3e;">🏁 START HUB</div>
                    <div class="lane-label">🡦 ONE-WAY RUN SUPPLY LANE (LEFT ➔ RIGHT)</div>
                    <div class="flow-arrow">➔ ➔</div>
                    {top_markers}
                    {top_car_tag}
                </div>

                <div class="runway-lane-track" style="margin-top: 110px; width: 95%;">
                    <div class="docking-terminal" style="background:#495057;">🛬 RETURN POINT</div>
                    <div class="lane-label" style="color:#c1c2c3;">🡧 ONE-WAY RETURN LANE (RIGHT ➔ LEFT)</div>
                    <div class="flow-arrow" style="left: 140px; right: auto;">➔ ➔</div>
                    {bottom_markers}
                    {bottom_car_tag}
                </div>
                
            </div>

            <div class="cards-outer-container">{info_cards}</div>
        </div>
        """

    if st.session_state.running:
        while st.session_state.running:
            advance_simulation(speed_acceleration)
            map_container_box.html(generate_html_floorplan())
            status_msg_box.info(f"🚜 **Vehicle Tracker:** Car status is currently `{st.session_state.tugger_status}` (Timer: `{st.session_state.process_timer}s`)")
            
            with kpi_metric_row.container():
                m1, m2, m3 = st.columns(3)
                m1.metric("⏱️ Operational Time Elapsed (MM:SS)", format_to_mmss(st.session_state.sim_time))
                m2.metric("🚜 Completed One-Way Loop Runs", f"{st.session_state.trip_counter} Loops")
                m3.metric("📦 Car Consigned Cargo Volume", f"{st.session_state.active_delivery_qty} units")
            
            time.sleep(0.04)
    else:
        map_container_box.html(generate_html_floorplan())
        status_msg_box.info(f"⏸️ **Simulation Paused:** `{st.session_state.tugger_status}`")

elif app_mode == "📊 Isolated Shortage Analytics":
    st.title("📊 Sub-Station Isolated Bottleneck Analysis Dashboard")
    st.markdown("Each child sub-station (drop point) is dynamically plotted below with independent telemetry lines to ensure accurate shortage diagnostics.")
    
    active_cols = list(st.session_state.chart_data.columns)
    
    if not active_cols:
        st.warning("No drop sub-station locations recorded on the track floorplan layout.")
    else:
        for col_name in active_cols:
            ws_part, sub_part = col_name.split("_")
            with st.container(border=True):
                c_left, c_right = st.columns([1, 4])
                with c_left:
                    st.subheader(f"📍 {ws_part} - {sub_part}")
                    shortage_duration_secs = st.session_state.starvation_events.get(col_name, 0)
                    st.metric(
                        "🚨 Accumulated Shortage", 
                        format_to_mmss(shortage_duration_secs), 
                        delta="Shortage Risk" if shortage_duration_secs > 0 else "Stable", 
                        delta_color="inverse"
                    )
                    
                    ws_d = st.session_state.workstations.get(ws_part, {})
                    if ws_d:
                        st.caption(f"**WS Sequence Position:** #{ws_d['sequence_order']}")
                        st.caption(f"**Stagger Offset:** {format_to_mmss((ws_d['sequence_order']-1) * master_takt_secs)}")
                        st.caption(f"**ROP Target:** {ws_d['sub_stations'][sub_part]['rop']} units")
                
                with c_right:
                    st.line_chart(st.session_state.chart_data[col_name].iloc[-400:], height=180)
        
        st.markdown("---")
        st.subheader("📋 Logistics Dispatch Historical Trip Logs Ledger")
        if not st.session_state.trip_log:
            st.info("No distribution cycles compiled into ledger yet. Initiate production workflow on the live layout page map.")
        else:
            st.dataframe(pd.DataFrame(st.session_state.trip_log), use_container_width=True)
