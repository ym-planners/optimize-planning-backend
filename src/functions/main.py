from firebase_functions import https_fn
from firebase_admin import initialize_app, firestore
import google.cloud.firestore # Required for SERVER_TIMESTAMP
from ortools.sat.python import cp_model
import json
import random
import traceback
import datetime 

initialize_app()

# Helper function to get a random base cost for an item if not present
def get_base_cost(item_doc):
    if "baseCostPerItem" in item_doc and isinstance(item_doc["baseCostPerItem"], (int, float)):
        return item_doc["baseCostPerItem"]
    return random.uniform(1.5, 3.0)

@https_fn.on_request(region="europe-west1", memory=16384, cpu=8)
def optimizeProduction(req: https_fn.Request) -> https_fn.Response:
    cors_headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization'
    }

    if req.method == 'OPTIONS':
        return https_fn.Response("", headers=cors_headers, status=204)

    db: google.cloud.firestore.Client = firestore.client()
    payload = None
    if req.method == 'POST':
        try:
            payload = req.get_json(silent=True)
            if payload is None and req.data:
                 payload = json.loads(req.data)
            print(f"Received POST payload: {payload}") # Log received payload
        except Exception as e_payload:
            print(f"Error parsing POST payload: {e_payload}")
            # Proceed without payload if parsing fails, or return error
            # For now, proceed, effectively making payload optional on error

    try:
        # 1. Fetch Data from Firestore
        items_ref = db.collection("items")
        items_stream = items_ref.stream() # Use stream to check if empty
        items_data = {}
        for doc in items_stream:
            items_data[doc.id] = doc.to_dict()
            # Ensure baseCostPerItem is applied even if overridden later by payload
            items_data[doc.id]["baseCostPerItem"] = get_base_cost(items_data[doc.id])
        
        if not items_data:
             return https_fn.Response(
                json.dumps({"status": "error", "message": "No data found in items collection."}),
                status=400, headers={**cors_headers, "Content-Type": "application/json"})

        machines_ref = db.collection("machines")
        machines_stream = machines_ref.stream()
        machines_data = {}
        for doc in machines_stream:
            machines_data[doc.id] = doc.to_dict()

        if not machines_data:
            return https_fn.Response(
                json.dumps({"status": "error", "message": "No data found in machines collection."}),
                status=400, headers={**cors_headers, "Content-Type": "application/json"})

        # --- Parameter Overrides from Payload --- 
        STOCK_HOLDING_RATE_YEARLY_DEFAULT = 0.10
        STOCK_HOLDING_RATE_YEARLY = STOCK_HOLDING_RATE_YEARLY_DEFAULT

        if payload and isinstance(payload, dict): # Check if payload is a dict
            global_overrides = payload.get("global_overrides", {})
            if isinstance(global_overrides, dict): # Ensure global_overrides is a dict
                 STOCK_HOLDING_RATE_YEARLY = global_overrides.get("STOCK_HOLDING_RATE_YEARLY", STOCK_HOLDING_RATE_YEARLY_DEFAULT)
                 if not isinstance(STOCK_HOLDING_RATE_YEARLY, (int, float)) or not (0 <= STOCK_HOLDING_RATE_YEARLY <= 1):
                    print(f"Warning: Invalid STOCK_HOLDING_RATE_YEARLY in payload: {STOCK_HOLDING_RATE_YEARLY}. Using default.")
                    STOCK_HOLDING_RATE_YEARLY = STOCK_HOLDING_RATE_YEARLY_DEFAULT
            
            item_overrides_payload = payload.get("item_overrides", {})
            if isinstance(item_overrides_payload, dict): # Ensure item_overrides_payload is a dict
                for item_id, overrides in item_overrides_payload.items():
                    if item_id in items_data and isinstance(overrides, dict):
                        print(f"Applying overrides for item: {item_id}")
                        for key, value in overrides.items():
                            if key in ["operationTimePerPC", "baseCostPerItem", "FIXED_LOT_SIZE"] and isinstance(value, (int, float)) and value >=0:
                                items_data[item_id][key] = float(value)
                            elif key == "monthlyConsumption" and isinstance(value, dict):
                                items_data[item_id][key] = {str(m): int(c) for m, c in value.items() if isinstance(m, str) and isinstance(c, (int, float)) and c >=0}
                            else:
                                print(f"Warning: Invalid or unsupported override key/value for item {item_id}: {key}={value}")
            
            machine_overrides_payload = payload.get("machine_overrides", {})
            if isinstance(machine_overrides_payload, dict): # Ensure machine_overrides_payload is a dict
                for machine_id, overrides in machine_overrides_payload.items():
                    if machine_id in machines_data and isinstance(overrides, dict):
                        print(f"Applying overrides for machine: {machine_id}")
                        for key, value in overrides.items():
                            if key in ["dailyOperationalHours", "weeklyOperationalDays", "hourlyOperatingCost"] and isinstance(value, (int,float)) and value >=0:
                                 # Add validation for weeklyOperationalDays (1-7)
                                if key == "weeklyOperationalDays" and not (1 <= value <= 7):
                                    print(f"Warning: Invalid weeklyOperationalDays for machine {machine_id}: {value}. Skipping override.")
                                    continue
                                machines_data[machine_id][key] = float(value)
                            else:
                                print(f"Warning: Invalid or unsupported override key/value for machine {machine_id}: {key}={value}")
        # --- End Parameter Overrides ---

        model = cp_model.CpModel()
        MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
        NUM_MONTHS = len(MONTHS)
        DAYS_IN_MONTH = 20
        STOCK_HOLDING_RATE_MONTHLY = STOCK_HOLDING_RATE_YEARLY / NUM_MONTHS
        
        production_qty, is_producing, inventory_level = {}, {}, {}
        item_ids, machine_ids = list(items_data.keys()), list(machines_data.keys())

        for item_id in item_ids:
            item = items_data[item_id]
            item_op_time = item.get("operationTimePerPC", 1.0); item_op_time = float(item_op_time) if isinstance(item_op_time, (int,float)) and item_op_time > 0 else 1.0
            current_monthly_consumption = item.get("monthlyConsumption", {m: 0 for m in MONTHS})
            max_prod_for_item = sum(current_monthly_consumption.get(m, 0) for m in MONTHS) * 2 + 1

            for month_idx in range(NUM_MONTHS):
                inventory_level[(item_id, month_idx)] = model.NewIntVar(0, max_prod_for_item * NUM_MONTHS, f"inv_{item_id}_m{month_idx}")
                for machine_id in machine_ids:
                    machine_info = machines_data[machine_id]
                    machine_available_minutes_per_month = machine_info.get("dailyOperationalHours", 24) * machine_info.get("weeklyOperationalDays", 5) * DAYS_IN_MONTH * 60
                    max_prod_on_machine = 1
                    if item_op_time > 0: max_prod_on_machine = int(machine_available_minutes_per_month / item_op_time)
                    if max_prod_on_machine <= 0: max_prod_on_machine = 1
                    
                    var_key = (item_id, machine_id, month_idx)
                    production_qty[var_key] = model.NewIntVar(0, max_prod_on_machine, f"prod_{item_id}_{machine_id}_m{month_idx}")
                    is_producing[var_key] = model.NewBoolVar(f"isprod_{item_id}_{machine_id}_m{month_idx}")
                    model.Add(production_qty[var_key] > 0).OnlyEnforceIf(is_producing[var_key])
                    model.Add(production_qty[var_key] == 0).OnlyEnforceIf(is_producing[var_key].Not())

        for item_id in item_ids:
            item = items_data[item_id]
            previous_month_inventory = 0 
            current_monthly_consumption = item.get("monthlyConsumption", {m: 0 for m in MONTHS})
            for month_idx in range(NUM_MONTHS):
                month_name = MONTHS[month_idx]
                consumed_this_month = current_monthly_consumption.get(month_name, 0)
                consumed_this_month = int(consumed_this_month) if isinstance(consumed_this_month, (int, float)) else 0
                produced_this_month_on_all_machines = sum(production_qty[(item_id, m_id, month_idx)] for m_id in machine_ids)
                model.Add(inventory_level[(item_id, month_idx)] == previous_month_inventory + produced_this_month_on_all_machines - consumed_this_month)
                model.Add(inventory_level[(item_id, month_idx)] >= 0) 
                previous_month_inventory = inventory_level[(item_id, month_idx)]

        for machine_id in machine_ids:
            machine = machines_data[machine_id]
            for month_idx in range(NUM_MONTHS):
                total_time_on_machine_this_month = []
                for item_id in item_ids:
                    item = items_data[item_id]
                    item_op_time = item.get("operationTimePerPC", 1.0); item_op_time = float(item_op_time) if isinstance(item_op_time, (int,float)) and item_op_time > 0 else 1.0
                    total_time_on_machine_this_month.append(production_qty[(item_id, machine_id, month_idx)] * int(item_op_time * 100)) # Scaled op_time
                machine_available_minutes_per_month = int(machine.get("dailyOperationalHours", 24) * machine.get("weeklyOperationalDays", 5) * DAYS_IN_MONTH * 60 * 100) # Scaled capacity
                model.Add(sum(total_time_on_machine_this_month) <= machine_available_minutes_per_month)
        
        total_machining_cost_terms, total_stock_keeping_cost_terms = [], []
        for item_id in item_ids:
            item = items_data[item_id]
            item_cost = item.get("baseCostPerItem", 2.0); item_cost = float(item_cost) if isinstance(item_cost, (int,float)) else 2.0
            item_op_time = item.get("operationTimePerPC", 1.0); item_op_time = float(item_op_time) if isinstance(item_op_time, (int,float)) and item_op_time > 0 else 1.0
            current_monthly_consumption = item.get("monthlyConsumption", {m: 0 for m in MONTHS})
            max_prod_for_item = sum(current_monthly_consumption.get(m, 0) for m in MONTHS) * 2 + 1
            for month_idx in range(NUM_MONTHS):
                scaled_item_cost_monthly_holding = int(item_cost * STOCK_HOLDING_RATE_MONTHLY * 100) # Scale cost for multiplication
                stock_cost_term = model.NewIntVar(0, max_prod_for_item * NUM_MONTHS * scaled_item_cost_monthly_holding , f"stock_cost_{item_id}_m{month_idx}")
                model.AddMultiplicationEquality(stock_cost_term, [inventory_level[(item_id, month_idx)], scaled_item_cost_monthly_holding])
                total_stock_keeping_cost_terms.append(stock_cost_term)
                for machine_id in machine_ids:
                    machine = machines_data[machine_id]
                    machine_hourly_cost = machine.get("hourlyOperatingCost", 50.0); machine_hourly_cost = float(machine_hourly_cost) if isinstance(machine_hourly_cost, (int,float)) else 50.0
                    machine_available_minutes_per_month_for_obj = machine.get("dailyOperationalHours", 24) * machine.get("weeklyOperationalDays", 5) * DAYS_IN_MONTH * 60
                    max_prod_on_machine_for_obj = 1 
                    if item_op_time > 0: max_prod_on_machine_for_obj = int(machine_available_minutes_per_month_for_obj / item_op_time)
                    if max_prod_on_machine_for_obj <= 0 : max_prod_on_machine_for_obj = 1
                    cost_per_pc_on_machine_scaled = int(item_op_time * (machine_hourly_cost / 60.0) * 10000) 
                    machining_cost_for_prod_qty_scaled = model.NewIntVar(0, cost_per_pc_on_machine_scaled * max_prod_on_machine_for_obj , f"mach_cost_prod_qty_{item_id}_{machine_id}_m{month_idx}")
                    model.AddMultiplicationEquality(machining_cost_for_prod_qty_scaled, [production_qty[(item_id, machine_id, month_idx)], cost_per_pc_on_machine_scaled] )
                    total_machining_cost_terms.append(machining_cost_for_prod_qty_scaled)
        
        placeholder_eur_to_sek_rate = 10 
        scaled_total_machining_cost = sum(total_machining_cost_terms) 
        scaled_total_stock_keeping_cost_eur_units = sum(total_stock_keeping_cost_terms) 
        scaled_total_stock_keeping_cost_sek_equivalent = scaled_total_stock_keeping_cost_eur_units * placeholder_eur_to_sek_rate * 100 # Convert EUR (scaled by 100) to SEK (scaled by 10000)

        model.Minimize(scaled_total_machining_cost + scaled_total_stock_keeping_cost_sek_equivalent)
        
        solver = cp_model.CpSolver()
        solver.parameters.log_search_progress = True 
        solver.parameters.max_time_in_seconds = 120.0 
        status = solver.Solve(model)

        if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
            optimized_plan_details = []
            total_optimized_machining_cost_sek_val = 0
            total_optimized_stock_cost_eur_val = 0
            for item_id_loop in item_ids: # Use different var name to avoid conflict with outer scope
                item_loop = items_data[item_id_loop]
                item_cost_eur = item_loop.get("baseCostPerItem", 2.0); item_cost_eur = float(item_cost_eur) if isinstance(item_cost_eur, (int,float)) else 2.0
                item_op_time_loop = item_loop.get("operationTimePerPC", 1.0); item_op_time_loop = float(item_op_time_loop) if isinstance(item_op_time_loop, (int,float)) and item_op_time_loop > 0 else 1.0
                for month_idx_loop in range(NUM_MONTHS):
                    month_name_loop = MONTHS[month_idx_loop]
                    inv_level_val = solver.Value(inventory_level[(item_id_loop, month_idx_loop)])
                    stock_cost_this_month_eur = inv_level_val * item_cost_eur * STOCK_HOLDING_RATE_MONTHLY # Uses potentially overridden STOCK_HOLDING_RATE_MONTHLY
                    total_optimized_stock_cost_eur_val += stock_cost_this_month_eur
                    for machine_id_loop in machine_ids:
                        machine_loop = machines_data[machine_id_loop]
                        machine_hourly_cost_sek = machine_loop.get("hourlyOperatingCost", 50.0); machine_hourly_cost_sek = float(machine_hourly_cost_sek) if isinstance(machine_hourly_cost_sek, (int,float)) else 50.0
                        qty = solver.Value(production_qty[(item_id_loop, machine_id_loop, month_idx_loop)])
                        if qty > 0:
                            op_time_used_minutes = qty * item_op_time_loop
                            machining_cost_sek = (op_time_used_minutes / 60.0) * machine_hourly_cost_sek
                            total_optimized_machining_cost_sek_val += machining_cost_sek
                            optimized_plan_details.append({
                                "month": month_name_loop, "machineId": machine_id_loop, "itemId": item_id_loop,
                                "quantity": qty, "operationTimeUsedMinutes": round(op_time_used_minutes, 2),
                                "machiningCostSEK": round(machining_cost_sek, 2)
                            })
            
            total_original_machining_cost_sek_val = 0
            total_original_stock_cost_eur_val = 0
            for item_id_loop in item_ids:
                 item_loop = items_data[item_id_loop] # Use potentially overridden item data
                 item_cost_eur = item_loop.get("baseCostPerItem", 2.0); item_cost_eur = float(item_cost_eur) if isinstance(item_cost_eur, (int,float)) else 2.0
                 current_monthly_consumption_loop = item_loop.get("monthlyConsumption", {m: 0 for m in MONTHS})
                 total_demand_year = sum(current_monthly_consumption_loop.get(m,0) for m in MONTHS)
                 fixed_lot_size = item_loop.get("FIXED_LOT_SIZE", total_demand_year / 4 if total_demand_year > 0 else 50) 
                 if not isinstance(fixed_lot_size, (int,float)) or fixed_lot_size <= 0 : fixed_lot_size = 50
                 avg_op_time = item_loop.get("operationTimePerPC", 1.0); avg_op_time = float(avg_op_time) if isinstance(avg_op_time, (int,float)) and avg_op_time > 0 else 1.0
                 avg_machine_cost_hr_sek = 50.0 
                 if machine_ids:
                     first_machine_id = machine_ids[0]
                     # Base original cost on the first machine's potentially overridden cost or default
                     avg_machine_cost_hr_sek = machines_data[first_machine_id].get("hourlyOperatingCost",50.0)
                     if not isinstance(avg_machine_cost_hr_sek, (int,float)): avg_machine_cost_hr_sek = 50.0

                 original_machining_cost_item_sek = (total_demand_year * avg_op_time / 60.0 * avg_machine_cost_hr_sek)
                 total_original_machining_cost_sek_val += original_machining_cost_item_sek
                 original_stock_cost_item_eur = (fixed_lot_size / 2.0 * item_cost_eur * STOCK_HOLDING_RATE_YEARLY) # Use potentially overridden STOCK_HOLDING_RATE_YEARLY
                 total_original_stock_cost_eur_val += original_stock_cost_item_eur

            machining_savings_sek = total_original_machining_cost_sek_val - total_optimized_machining_cost_sek_val
            stock_savings_eur = total_original_stock_cost_eur_val - total_optimized_stock_cost_eur_val

            response_data = {
                "status": "success" if status == cp_model.OPTIMAL else "feasible",
                "message": solver.StatusName(status),
                "totalOptimizedMachiningCostSEK": round(total_optimized_machining_cost_sek_val,2),
                "totalOptimizedStockCostEUR": round(total_optimized_stock_cost_eur_val,2),
                "totalOriginalMachiningCostSEK": round(total_original_machining_cost_sek_val, 2),
                "totalOriginalStockCostEUR": round(total_original_stock_cost_eur_val, 2),
                "machiningSavingsSEK": round(machining_savings_sek, 2),
                "stockSavingsEUR": round(stock_savings_eur, 2),
                "plan": optimized_plan_details,
            }
            
            plan_to_save = response_data.copy()
            plan_to_save['createdAt'] = google.cloud.firestore.SERVER_TIMESTAMP
            if payload: # Log that this plan was generated with overrides
                plan_to_save['overrides_applied'] = True 

            try:
                plans_ref = db.collection('production_plans')
                plans_ref.add(plan_to_save)
                print("Production plan successfully saved to Firestore.")
            except Exception as e_save:
                print(f"Error saving production plan to Firestore: {e_save}")

            return https_fn.Response(
                json.dumps(response_data, indent=2), 
                status=200, headers={**cors_headers, "Content-Type": "application/json"})
        else:
            return https_fn.Response(
                json.dumps({"status": "error", "message": f"Optimization failed. Status: {solver.StatusName(status)}"}),
                status=500, headers={**cors_headers, "Content-Type": "application/json"})

    except Exception as e:
        tb_str = traceback.format_exc()
        # Corrected f-string for error_message
        error_message = f"""Error in optimizeProduction: {str(e)}
Traceback:
{tb_str}"""
        print(error_message)
        return https_fn.Response(
            json.dumps({"status": "error", "message": str(e), "trace": tb_str}),
            status=500, headers={**cors_headers, "Content-Type": "application/json"})
