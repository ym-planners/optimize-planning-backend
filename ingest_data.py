# ingest_data.py
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
import random
import math

# Initialize Firebase Admin SDK
SERVICE_ACCOUNT_KEY_PATH = '../../qwiklabs-gcp-00-6d5f50f68707-firebase-adminsdk-fbsvc-9b5f1a76d2.json'
EXCEL_FILE_PATH = '../../data/turning-data.xlsx'
CSV_ITEMS_FALLBACK_PATH = '../../data/turning-data.csv'

def initialize_firebase():
    try:
        cred = credentials.Certificate(SERVICE_ACCOUNT_KEY_PATH)
        if not firebase_admin._apps: # Check if already initialized to prevent re-initialization error
            firebase_admin.initialize_app(cred)
        return firestore.client()
    except Exception as e:
        print(f"Error initializing Firebase Admin SDK: {e}")
        print("Please ensure you have a valid service account key file and the path is correct.")
        exit()
    
# Helper functions to parse values, handling potential errors and formatting issues
def parse_operation_time(op_time_str):
    if pd.isna(op_time_str) or str(op_time_str).lower() == 'nan':
        return 0.0
    try:
        return float(str(op_time_str).replace(',', '.').upper().replace(' MIN', '').strip())
    except ValueError:
        return 0.0

def parse_float_with_comma(value_str):
    if pd.isna(value_str) or str(value_str).lower() == 'nan':
        return 0.0
    try:
        return float(str(value_str).replace(',', '.').strip())
    except ValueError:
        return 0.0

# Function to ingest items from a DataFrame
def ingest_items(db, df):
    items_collection_name = 'items'
    items_ref = db.collection(items_collection_name)
    print(f"Starting items ingestion into '{items_collection_name}' collection...")

    df.columns = [str(col).strip() for col in df.columns]

    col_item_id = 'Item Id'
    col_op_time = 'Operation Time Per PC'
    col_material_length = 'Material Length (mm)'
    col_machine_id = 'Machine Id'
    col_forecast = 'FORECAST_YEAR'
    col_fixed_lot = 'FIXED_LOT_SIZE'
    col_raw_material_id = 'RawMaterial Id'

    consumption_column_map = {
        'Consumed January': 'January', 'Consumed February': 'February', 'Consumed March': 'March',
        'Consumed April': 'April', 'Consumed May': 'May', 'Consumed June': 'June',
        'Consumed July': 'July', 'Consumed August': 'August', 'Consumed September': 'September',
        'Consumed October': 'October', 'Consumed November': 'November', 'Consumed December': 'December'
    }
    count = 0
    for index, row in df.iterrows():
        try:
            item_id_val = str(row[col_item_id]).strip()
            if not item_id_val or item_id_val.lower() == 'nan':
                print(f"Skipping item row {index+2} due to missing or invalid Item Id.")
                continue

            monthly_consumption = {}
            for excel_col, firestore_month in consumption_column_map.items():
                val = 0
                if excel_col in row and not pd.isna(row[excel_col]):
                    try:
                        val = int(parse_float_with_comma(row[excel_col]))
                    except ValueError:
                        val = 0
                monthly_consumption[firestore_month] = val

            item_data = {
                'itemId': item_id_val,
                'operationTimePerPC': parse_operation_time(row.get(col_op_time)),
                'materialLengthMM': parse_float_with_comma(row.get(col_material_length)),
                'currentMachineId': str(row.get(col_machine_id, '')).strip(),
                'rawMaterialId': str(row.get(col_raw_material_id, '')).strip(),
                'forecastYear': int(parse_float_with_comma(row.get(col_forecast, 0))),
                'FIXED_LOT_SIZE': int(parse_float_with_comma(row.get(col_fixed_lot, 0))),
                'monthlyConsumption': monthly_consumption,
                'baseCostPerItem': round(random.uniform(1.5, 3.0), 2)
            }
            items_ref.document(item_id_val).set(item_data)
            count += 1
        except KeyError as ke:
            print(f"KeyError for item row {index+2} (Item ID: {row.get(col_item_id, 'Unknown')}): Missing column {ke}.")
        except Exception as e: # Catch other potential errors during row processing
            print(f"Error ingesting item row {index+2} (Item ID: {row.get(col_item_id, 'Unknown')}): {e}")
    print(f"Items ingestion complete. {count} items ingested.")

# Function to ingest machines using the mapping and specification data
def ingest_machines(db, machine_mapping_df, machine_spec_df):
    machines_collection_name = 'machines'
    machines_ref = db.collection(machines_collection_name)
    print(f"Starting machines ingestion into '{machines_collection_name}' collection using mapping and specification...")

    # Normalize column names for both dataframes
    machine_mapping_df.columns = [str(col).strip() for col in machine_mapping_df.columns]
    machine_spec_df.columns = [str(col).strip() for col in machine_spec_df.columns]

    # Create the machine type to actual ID mapping
    machine_type_to_ids = {}
    for index, row in machine_mapping_df.iterrows():
        try:
            machine_type = str(row['Machine Type']).strip()
            actual_machine_id = str(row['Actual Machine ID']).strip()
            if machine_type and actual_machine_id:
                if machine_type not in machine_type_to_ids:
                    machine_type_to_ids[machine_type] = []
                machine_type_to_ids[machine_type].append(actual_machine_id)
        except KeyError as ke:
             print(f"KeyError reading Machine Mapping row {index+2}: Missing column {ke}. Skipping row.")
        except Exception as e:
            print(f"Error processing Machine Mapping row {index+2}: {e}")

    ingested_count = 0
    # Iterate through the mapping to create machine documents
    for machine_type, actual_ids in machine_type_to_ids.items():
        # Find the corresponding specification row(s) for this machine type
        spec_rows = machine_spec_df[machine_spec_df['Machine Type'].str.strip() == machine_type]

        if spec_rows.empty:
            print(f"Warning: No machine specification found for type '{machine_type}'. Skipping ingestion for these IDs.")
            continue

        # Assuming one spec row per machine type for simplicity based on screenshot
        spec_row = spec_rows.iloc[0]

        for actual_id in actual_ids:
            try:
                machine_data = {
                    'machineType': machine_type,
                    'actualMachineId': actual_id,
                    'dailyOperationalHours': 24,  # Default from PRD
                    'weeklyOperationalDays': 5,    # Default from PRD
                    'hourlyOperatingCost': parse_float_with_comma(spec_row.get('Cost per minute in SEK', 0.0)) * 60,  # In SEK
                    'turretCapacity': int(spec_row.get('Tool Capacity Turret 1', 0)),
                    'toolCapacityTurret2': int(spec_row.get('Tool Capacity Turret 2', 0)),
                    'toolCapacityMillingSpindle': int(spec_row.get('Tool Capacity Milling Spindle', 0)),
                    'speedUpFactor': parse_float_with_comma(spec_row.get('Speed up factor', 1.0)),
                    'toolChangeTimeMinutes': 5, # Default from PRD
                    'rawMaterialChangeTimeMinutes': 20, # Default from PRD
                }
                machines_ref.document(actual_id).set(machine_data)
                ingested_count += 1
                # print(f"Ingested machine: {actual_id} (Type: {machine_type})")
            except Exception as e:
                 print(f"Error ingesting machine with Actual ID '{actual_id}' (Type: {machine_type}): {e}")

    print(f"Machines ingestion complete. {ingested_count} machine instances ingested.")

def create_default_machines(db):
    machines_collection_name = 'machines'
    machines_ref = db.collection(machines_collection_name)
    print(f"Creating default machine set in '{machines_collection_name}' collection as fallback...")
    default_machines = {
        "M1": {'machineId': "M1", 'machineType': "DefaultTypeA", 'dailyOperationalHours': 24, 'weeklyOperationalDays': 5, 'hourlyOperatingCost': 50.0, 'turretCapacity': 12, 'speedUpFactor': 1.0, 'toolChangeTimeMinutes': 5, 'rawMaterialChangeTimeMinutes': 20},
        "M2": {'machineId': "M2", 'machineType': "DefaultTypeA", 'dailyOperationalHours': 24, 'weeklyOperationalDays': 5, 'hourlyOperatingCost': 55.0, 'turretCapacity': 12, 'speedUpFactor': 1.0, 'toolChangeTimeMinutes': 5, 'rawMaterialChangeTimeMinutes': 20},
        "M3": {'machineId': "M3", 'machineType': "DefaultTypeB", 'dailyOperationalHours': 24, 'weeklyOperationalDays': 5, 'hourlyOperatingCost': 50.0, 'turretCapacity': 10, 'speedUpFactor': 1.0, 'toolChangeTimeMinutes': 5, 'rawMaterialChangeTimeMinutes': 20},
        "M4": {'machineId': "M4", 'machineType': "DefaultTypeB", 'dailyOperationalHours': 24, 'weeklyOperationalDays': 5, 'hourlyOperatingCost': 52.0, 'turretCapacity': 10, 'speedUpFactor': 1.0, 'toolChangeTimeMinutes': 5, 'rawMaterialChangeTimeMinutes': 20},
    }
    for m_id, data in default_machines.items():
        try:
            machines_ref.document(m_id).set(data)
        except Exception as e:
            print(f"Error creating default machine {m_id}: {e}")
    print("Default machines (M1-M4) ingestion complete.")

def main():
    db = initialize_firebase()
    items_data_loaded = False
    machines_data_loaded = False

    try:
        print(f"Attempting to read Excel file: {EXCEL_FILE_PATH}")
        xls = pd.ExcelFile(EXCEL_FILE_PATH)
        if 'Items' in xls.sheet_names: # Check if 'Items' sheet exists before parsing
            print("Found 'Items' sheet. Parsing item data from Excel...")
            items_df_excel = xls.parse('Items')
            ingest_items(db, items_df_excel)
            items_data_loaded = True
        else:
            print("Warning: 'Items' sheet not found in Excel.")

        # Check for both 'Machine Mapping' and 'Machine Specification' sheets
        machine_mapping_loaded = 'Machine Mapping' in xls.sheet_names
        machine_spec_loaded = 'Machine Specification' in xls.sheet_names

        if machine_mapping_loaded and machine_spec_loaded:
            print("Found 'Machine Mapping' and 'Machine Specification' sheets. Parsing machine data from Excel...")
            machine_mapping_df_excel = xls.parse('Machine Mapping')
            print("Found 'Machine Specification' sheet. Parsing machine data from Excel...")
            machines_df_excel = xls.parse('Machine Specification')
            ingest_machines(db, machine_mapping_df_excel, machines_df_excel)
            machines_data_loaded = True
        elif not machine_mapping_loaded and machine_spec_loaded:
             print("Warning: 'Machine Mapping' sheet not found in Excel, but 'Machine Specification' was found. Cannot link actual IDs without the mapping.")
        elif machine_mapping_loaded and not machine_spec_loaded:
             print("Warning: 'Machine Specification' sheet not found in Excel, but 'Machine Mapping' was found. Cannot get machine specs without the specification sheet.")

            
    except FileNotFoundError:
        print(f"Error: Excel file '{EXCEL_FILE_PATH}' not found.")
    except Exception as e_excel:
        print(f"General error reading Excel file '{EXCEL_FILE_PATH}': {e_excel}")

    # Fallback for Items if not loaded from Excel
    if not items_data_loaded:
        print(f"Attempting fallback to CSV for item data: {CSV_ITEMS_FALLBACK_PATH}")
        try:
            items_df_csv = pd.read_csv(CSV_ITEMS_FALLBACK_PATH, delimiter=';')
            print("Successfully read item data from CSV.")
            ingest_items(db, items_df_csv)
            items_data_loaded = True # Mark as loaded if CSV is successful
        except FileNotFoundError:
            print(f"Error: CSV fallback file '{CSV_ITEMS_FALLBACK_PATH}' not found.")
        except Exception as e_csv:
            print(f"Error reading CSV file '{CSV_ITEMS_FALLBACK_PATH}': {e_csv}")

    # Fallback for Machines if not loaded from Excel
    if not machines_data_loaded:
        print("Machine data not loaded from Excel. Creating default machines.")
        create_default_machines(db)
        machines_data_loaded = True # Mark as loaded if defaults are created
        
    if not items_data_loaded and not machines_data_loaded:
        print("Critical error: No data could be loaded for items or machines. Exiting.")
        exit()
        
    print("Data ingestion script finished.")

if __name__ == "__main__":
    main()
