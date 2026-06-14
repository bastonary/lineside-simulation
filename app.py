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

# --- 2. INITIALIZE GLOBAL STATE ENGINE ---
if "sim_time" not in st.session_state:
    st.session_state.sim_time = 0          
    st.session_state.trip_log = []         
    st.session_state.starvation_events = {}  
    st.session_state.tugger_status = "Idle at Store"
    st.session_state.process_timer = -1   
    st.session_state.max_transit_secs = 1
    st.session_state.trip_counter = 0      
    st.session_state.running = False       
    st.session_state.tugger_pct = 0.0      
    st.session_state.trip_start_time = 0   
    st.session_state.active_delivery_qty = 0
    st.session_state.current_target_point = None

    # NESTED CONFIG: Drop locations placed on a unified 0-100% closed-loop delivery track
    st.session_state.station_groups = {
        "Main Assembly Line": {
            "RA110": {"inventory": 23, "rop": 15, "qty_per_pkg": 10, "pkgs_per_trip": 3, "distance_pct": 25, "sequence_order": 3},
            "RA120": {"inventory": 24, "rop": 15, "qty_per_pkg": 12, "pkgs_per_trip": 4, "distance_pct": 50, "sequence_order": 2},
            "RA130": {"inventory": 25, "rop": 12, "qty_per_pkg": 8,  "pkgs_per_trip": 5, "distance_pct": 75, "sequence_order": 1}
        }
    }
    
    for g_name, pts in st.session_state.station_groups.items():
        for p_name in pts.keys():
            st.session_state.starvation_events[p_name] = 0

    snap = {p: d["inventory"] for g in st.session_state.station_groups.values() for p, d in g.items()}
    st.session_state.chart_data = pd.DataFrame([snap])

# --- 3. SIDEBAR CONFIGURATIONS DECK ---
st.sidebar.title("🎮 Factory Control Room")
app_mode = st.sidebar.selectbox("📂 Select Dashboard Page", ["🗺️ Live Simulation Map", "📊 Isolated Shortage Analytics"])

st.sidebar.header("⚙️ Master Line Rate Settings")
master_takt_mins = st.sidebar.number_input("Whole Line Master Takt (Minutes)", min_value=1.0, value=8.0, step=0.5)
master_takt_secs = int(master_takt_mins * 60)

st.sidebar.header("➕ Modify & Add Drop Points")
new_point_name = st.sidebar.text_input("New Drop Point Name:", placeholder="e.g., RA140")
new_point_seq = st.sidebar.number_input("Adjust Sequence Order Position (e.g. 1, 2, 3...):", min_value=1, value=4)

if st.sidebar.button("💾 Deploy Drop Point to Line"):
    if new_point_name:
        calculated_stock = 20
        all_pts = st.session_state.station_groups["Main Assembly Line"]
        if all_pts:
            last_key = list(all_pts.keys())[-1]
            calculated_stock = all_pts[last_key]["inventory"] + 1

        st.session_state.station_groups["Main Assembly Line"][new_point_name] = {
            "inventory": calculated_stock,
            "rop": 15,
            "qty_per_pkg": 10,
            "pkgs_per_trip": 3,
            "distance_pct": min(90, len(all_pts) * 22 + 15),
            "sequence_order": new_point_seq
        }
        st.session_state.starvation_events[new_point_name] = 0
        st.toast(f"Deployed point {new_point_name} with Sequence position {new_point_seq}!", icon="✅")
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.subheader("📐 Fine-Tune Node Operations")
all_active_points = [p for g in st.session_state.station_groups.values() for p in g.keys()]
selected_point = st.sidebar.selectbox("Select Point to Modify:", all_active_points if all_active_points else ["None"])

if selected_point and selected_point != "None":
    pt_ref = st.session_state.station_groups["Main Assembly Line"][selected_point]
    st.markdown(f"**Modifying Node:** `{selected_point}`")
    pt_ref["inventory"] = st.sidebar.number_input("Live Stock Level (Units)", min_value=0, value=int(pt_ref["inventory"]))
    pt_ref["rop"] = st.sidebar.number_input("Reorder Point Threshold (ROP)", min_value=0, value=int(pt_ref["rop"]))
    pt_ref["sequence_order"] = st.sidebar.number_input("Adjust Sequence Order Position:", min_value=1, value=int(pt_ref["sequence_order"]))
    pt_ref["qty_per_pkg"] = st.sidebar.number_input("Units Per Package Box", min_value=1, value=int(pt_ref["qty_per_pkg"]))
    pt_ref["pkgs_per_trip"] = st.sidebar.number_input("Package Boxes Per Trip", min_value=1, value=int(pt_ref["pkgs_per_trip"]))
    pt_ref["distance_pct"] = st.sidebar.slider("Circuit Track Location Stop (%)", 5, 95, value=int(pt_ref["distance_pct"]))

    if st.sidebar.button("🗑️ Delete Drop Point from Line", type="primary"):
        del st.session_state.station_groups["Main Assembly Line"][selected_point]
        if selected_point in st.session_state.starvation_events:
            del st.session_state.starvation_events[selected_point]
        st.toast(f"Removed drop point {selected_point}")
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.header("⚡ Simulation Physics Engine")
speed_acceleration = st.sidebar.slider("Simulation Acceleration (Seconds/Frame)", min_value=1, max_value=60, value=15)

if st.session_state.chart_data.empty or set(all_active_points) != set(st.session_state.chart_data.columns):
    snap = {p: d["inventory"] for g in st.session_state.station_groups.values() for p, d in g.items()}
    st.session_state.chart_data = pd.DataFrame([snap])

# --- 4. CLOSED-LOOP RECALCULATION LOGIC ---
def advance_simulation(seconds):
    for _ in range(int(seconds)):
        st.session_state.sim_time += 1
        
        # A. Sequence Takt Stagger Loop Logic
        for g_name, pts in st.session_state.station_groups.items():
            for p_name, data in pts.items():
                stagger_offset = (data["sequence_order"] - 1) * master_takt_secs
                target_trigger_time = st.session_state.sim_time - stagger_offset
                
                if target_trigger_time > 0 and target_trigger_time % master_takt_secs == 0:
                    if data["inventory"] > 0:
                        data["inventory"] -= 1
                    else:
                        st.session_state.starvation_events[p_name] += 1

        # B. Circular Loop Fleet Delivery Pathing Routine
        if st.session_state.tugger_status == "Idle at Store":
            st.session_state.tugger_pct = 0.0
            
            highest_urgency = -9999
            chosen_p = None
            chosen_g = None
            
            for g_name, pts in st.session_state.station_groups.items():
                for p_name, data in pts.items():
                    if data["inventory"] <= data["rop"]:
                        urgency = data["rop"] - data["inventory"]
                        if urgency > highest_urgency:
                            highest_urgency = urgency
                            chosen_p = p_name
                            chosen_g = g_name
            
            if chosen_p:
                st.session_state.current_target_point = (chosen_g, chosen_p)
                p_data = st.session_state.station_groups[chosen_g][chosen_p]
                st.session_state.active_delivery_qty = p_data["qty_per_pkg"] * p_data["pkgs_per_trip"]
                
                st.session_state.tugger_status = f"Loading for {chosen_p}"
                st.session_state.trip_start_time = st.session_state.sim_time
                st.session_state.process_timer = 20  
                st.session_state.max_transit_secs = 20
                
        elif st.session_state.tugger_status.startswith("Loading for"):
            st.session_state.process_timer -= 1
            if st.session_state.process_timer <= 0:
                g, p = st.session_state.current_target_point
                st.session_state.tugger_status = f"Transit to {p}"
                st.session_state.process_timer = 45  
                st.session_state.max_transit_secs = 45
                
        elif st.session_state.tugger_status.startswith("Transit to"):
            st.session_state.process_timer -= 1
            g, p = st.session_state.current_target_point
            max_pos = st.session_state.station_groups[g][p]["distance_pct"]
            
            elapsed = st.session_state.max_transit_secs - st.session_state.process_timer
            st.session_state.tugger_pct = (elapsed / st.session_state.max_transit_secs) * max_pos
            
            if st.session_state.process_timer <= 0:
                st.session_state.tugger_pct = max_pos  
                st.session_state.tugger_status = f"Unloading at {p} STOP"
                st.session_state.process_timer = 25  
                st.session_state.max_transit_secs = 25
                
        elif st.session_state.tugger_status.startswith("Unloading at"):
            st.session_state.process_timer -= 1
            if st.session_state.process_timer <= 0:
                g, p = st.session_state.current_target_point
                if g in st.session_state.station_groups and p in st.session_state.station_groups[g]:
                    st.session_state.station_groups[g][p]["inventory"] += st.session_state.active_delivery_qty
                
                st.session_state.trip_counter += 1
                st.session_state.tugger_status = "Continuing Circle Loop to Store"
                st.session_state.process_timer = 45
                st.session_state.max_transit_secs = 45
                
        elif st.session_state.tugger_status == "Continuing Circle Loop to Store":
            st.session_state.process_timer -= 1
            g, p = st.session_state.current_target_point
            max_pos = st.session_state.station_groups[g][p]["distance_pct"]
            
            # Continue around the path loop tracking forward to 100% capacity cap
            elapsed = st.session_state.max_transit_secs - st.session_state.process_timer
            ratio = elapsed / st.session_state.max_transit_secs
            st.session_state.tugger_pct = max_pos + (ratio * (100.0 - max_pos))
            
            if st.session_state.process_timer <= 0:
                duration_secs = st.session_state.sim_time - st.session_state.trip_start_time
                st.session_state.trip_log.append({
                    "Trip ID": f"TRP-{st.session_state.trip_counter:03d}",
                    "Drop Point Destination": p,
                    "Payload Delivered": f"{st.session_state.active_delivery_qty} units",
                    "Total Cycle Time": format_to_mmss(duration_secs)
                })
                st.session_state.tugger_status = "Idle at Store"
                st.session_state.current_target_point = None
                st.session_state.tugger_pct = 0.0

    snap = {p: d["inventory"] for g in st.session_state.station_groups.values() for p, d in g.items()}
    st.session_state.chart_data = pd.concat([st.session_state.chart_data, pd.DataFrame([snap])], ignore_index=True)

# --- 5. SYSTEM ACTION TOOLBAR ---
c1, c2, c3 = st.columns(3)
with c1:
    if st.button("▶️ Run Production", use_container_width=True):
        st.session_state.running = True
with c2:
    if st.button("⏸️ Freeze Production", use_container_width=True):
        st.session_state.running = False
with c3:
    if st.button("🔄 Reset Plant State", use_container_width=True):
        st.session_state.sim_time = 0
        st.session_state.trip_log = []
        st.session_state.tugger_status = "Idle at Store"
        st.session_state.current_target_point = None
        st.session_state.running = False
        st.session_state.tugger_pct = 0.0
        for p in st.session_state.starvation_events.keys():
            st.session_state.starvation_events[p] = 0
        st.rerun()

# --- 6. PAGE NAVIGATION ROUTER ---
if app_mode == "🗺️ Live Simulation Map":
    st.title("🗺️ Modern Factory Loop Tracker Map")
    st.markdown(f"**Current Strategy Configuration:** Whole line processing takt cycle is set to **{master_takt_mins} minutes** ({master_takt_secs}s). Stations are staggered sequentially based on their adjustable position sequence indices.")
    
    map_container_box = st.empty()
    status_msg_box = st.empty()
    kpi_metric_row = st.empty()

    def generate_html_floorplan():
        pct = st.session_state.tugger_pct
        tgt_tuple = st.session_state.current_target_point
        tgt_p = tgt_tuple[1] if tgt_tuple else None
        
        point_markers = ""
        info_cards = ""
        
        for g_name, pts in st.session_state.station_groups.items():
            for p_name, d in pts.items():
                pos = d["distance_pct"]
                is_targeted = "background: #c92a2a; color: white; border: 2px solid white; box-shadow: 0 0 14px #c92a2a;" if p_name == tgt_p else "background: #495057; color: #f8f9fa;"
                
                point_markers += f"""
                <div class="station-node-pin" style="left: {pos}%; {is_targeted}">
                    📍 {p_name} <br>
                    <span style="font-size:9px; opacity:0.85;">Seq Pos: #{d['sequence_order']}</span>
                </div>
                """
                
                card_style = "border-left: 5px solid #c92a2a; background-color: #fff5f5;" if p_name == tgt_p else "border-left: 5px solid #1c7ed6;"
                info_cards += f"""
                <div class="kpi-card-block" style="{card_style}">
                    <div style="font-weight:bold; font-size:12px; color:#212529;">Drop {p_name} (Pos #{d['sequence_order']})</div>
                    <div style="font-size:18px; font-weight:bold; color:#1a1b1c; margin:2px 0;">📦 Stock: {d['inventory']} u</div>
                    <div style="font-size:10px; color:#6c757d; line-height:1.2;">
                        ROP Trigger: {d['rop']} u <br>
                        Pkg Size: {d['qty_per_pkg']} x {d['pkgs_per_trip']} <br>
                        <span style="color:#fa5252; font-weight:bold;">🚨 Shortages: {format_to_mmss(st.session_state.starvation_events[p_name])}</span>
                    </div>
                </div>
                """
                
        # Tugger truck label and icon positioning assignment 
        is_returning = pct > (st.session_state.station_groups["Main Assembly Line"][tgt_p]["distance_pct"] if tgt_p else 100)
        tugger_label = "🚜 Backhaul to Store" if is_returning else f"🚜 Delivery to {tgt_p}"
        color_class = "returning" if is_returning else ""
        
        top_tugger_tag = f'<div class="tugger-truck {color_class}" style="left: {pct}%;">{tugger_label}</div>' if tgt_p or pct > 0 else ''

        return f"""
        <style>
            .floorplan-wrapper {{ background: #fafafa; border: 1px solid #dee2e6; border-radius: 12px; padding: 24px; font-family: system-ui, sans-serif; }}
            .flex-header-row {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 30px; }}
            .cards-container {{ display: flex; flex-wrap: wrap; gap: 12px; justify-content: flex-end; max-width: 80%; }}
            .kpi-card-block {{ background: #ffffff; border: 1px solid #e9ecef; padding: 10px 14px; border-radius: 6px; box-shadow: 0 2px 6px rgba(0,0,0,0.04); min-width:155px; }}
            .circle-loop-track {{ height: 22px; background: linear-gradient(90deg, #e9ecef 0%, #dee2e6 50%, #e9ecef 100%); border-top: 3px dashed #adb5bd; border-bottom: 3px dashed #adb5bd; position: relative; margin: 50px 0; border-radius: 12px; }}
            .depot-start-badge {{ position: absolute; left: 0%; top: -14px; background: #2b8a3e; color: white; padding: 4px 12px; border-radius: 4px; font-size: 10px; font-weight: bold; z-index: 15; transform: translateX(-50%); }}
            .depot-end-badge {{ position: absolute; left: 100%; top: -14px; background: #2b8a3e; color: white; padding: 4px 12px; border-radius: 4px; font-size: 10px; font-weight: bold; z-index: 15; transform: translateX(-50%); }}
            .station-node-pin {{ position: absolute; top: -18px; padding: 4px 10px; border-radius: 4px; font-size: 10px; font-weight: bold; transform: translateX(-50%); z-index: 12; text-align: center; line-height: 1.2; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }}
            .tugger-truck {{ position: absolute; top: -26px; background: #1c7ed6; color: white; padding: 4px 10px; border-radius: 4px; font-size: 10px; font-weight: bold; transform: translateX(-50%); z-index: 20; white-space: nowrap; transition: left 0.05s linear; box-shadow: 0 4px 10px rgba(0,0,0,0.15); }}
            .tugger-truck.returning {{ background: #fa5252; }}
        </style>
        <div class="floorplan-wrapper">
            <div class="flex-header-row">
                <div style="background: #2b8a3e; color: white; padding: 16px; border-radius: 8px; font-weight: bold; min-width: 170px; box-shadow: 0 2px 5px rgba(0,0,0,0.05);">
                    🏪 Supermarket Store<br><small style="font-weight:normal; opacity:0.85;">Central Staging Hub</small>
                </div>
                <div class="cards-container">{info_cards}</div>
            </div>
            
            <div style="position: relative; margin: 60px 30px 30px 30px;">
                <!-- Single Unified Continuous Delivery Loop Track Layout Line -->
                <div class="circle-loop-track">
                    <div class="depot-start-badge">🏁 DEPART</div>
                    {point_markers}
                    {top_tugger_tag}
                    <div class="depot-end-badge">🔄 RETURN</div>
                </div>
            </div>
        </div>
        """

    # --- RUNTIME CLOCK ACCELERATOR ANIMATION EXECUTION LOOP ---
    if st.session_state.running:
        while st.session_state.running:
            advance_simulation(speed_acceleration)
            map_container_box.html(generate_html_floorplan())
            status_msg_box.info(f"⚙️ **Tugger Fleet Status Monitor:** `{st.session_state.tugger_status}` (Task Countdown Timer: `{st.session_state.process_timer}s`)")
            
            with kpi_metric_row.container():
                m1, m2, m3 = st.columns(3)
                m1.metric("⏱️ Operational Time Elapsed (MM:SS)", format_to_mmss(st.session_state.sim_time))
                m2.metric("🚜 Completed Delivery Cycles", f"{st.session_state.trip_counter} Runs")
                m3.metric("📦 Current Transport Payload", f"{st.session_state.active_delivery_qty} units")
            
            time.sleep(0.04)
    else:
        map_container_box.html(generate_html_floorplan())
        status_msg_box.info(f"⏸️ **Simulation Interrupted/Paused:** `{st.session_state.tugger_status}`")

elif app_mode == "📊 Isolated Shortage Analytics":
    st.title("📊 Isolated Stockout Risk Analysis Panels")
    st.markdown("Each drop point contains an independent reporting layout stream to easily target bottlenecks.")
    
    active_cols = list(st.session_state.chart_data.columns)
    
    if not active_cols:
        st.warning("No line stop drop node configurations detected inside the plant registry layout.")
    else:
        for col_name in active_cols:
            with st.container(border=True):
                c_left, c_right = st.columns([1, 4])
                with c_left:
                    st.subheader(f"📍 Drop Station: {col_name}")
                    shortage_duration_secs = st.session_state.starvation_events.get(col_name, 0)
                    st.metric(
                        "🚨 Total Shortage Accumulated", 
                        format_to_mmss(shortage_duration_secs), 
                        delta="Critical Shortage" if shortage_duration_secs > 0 else "Optimal", 
                        delta_color="inverse"
                    )
                    
                    pt_info = st.session_state.station_groups["Main Assembly Line"].get(col_name, {})
                    if pt_info:
                        st.caption(f"**Sequence Stagger Delay:** {format_to_mmss((pt_info['sequence_order']-1) * master_takt_secs)} Behind Start")
                        st.caption(f"**Reorder Target Point (ROP):** {pt_info['rop']} units")
                
                with c_right:
                    st.line_chart(st.session_state.chart_data[col_name].iloc[-400:], height=180)
        
        st.markdown("---")
        st.subheader("📋 Logistics Dispatch Historical Trip Logs Ledger")
        if not st.session_state.trip_log:
            st.info("No distribution cycles compiled into ledger yet. Initiate production workflow on the live layout page map.")
        else:
            st.dataframe(pd.DataFrame(st.session_state.trip_log), use_container_width=True)
