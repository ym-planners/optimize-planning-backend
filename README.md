# fb-planners-app

This project is an AI-Powered Dynamic Lot Sizing & Machine Assignment tool, built as an MVP for a hackathon. It uses Firebase for backend services and Google OR-Tools for optimization.

## Project Setup and Deployment

This document outlines the steps to set up, deploy, and manage the data for the FB Planners App.

### Prerequisites

1.  **Firebase CLI:** Ensure you have the Firebase CLI installed and configured. (https://firebase.google.com/docs/cli#setup_the_cli)
2.  **Python 3.11:** The Cloud Functions and ingestion script are written in Python 3.11.
3.  **Google Cloud SDK (`gcloud`):** Required if you need to manage Cloud Run service permissions (e.g., for unauthenticated invocation).
4.  **Service Account Key:** For data ingestion, a Firebase service account key JSON file is needed. Place it in the project root directory (e.g., `qwiklabs-gcp-00-6d5f50f68707-firebase-adminsdk-fbsvc-9b5f1a76d2.json`). **Important:** This key should be kept secure and ideally not committed to public repositories.

### Folder Structure Overview

*   `config/`: Contains Firebase configuration files like `firestore.rules` and `firestore.indexes.json`.
*   `data/`: For storing data files (e.g., `turning-data.xlsx`, `turning-data.csv`).
*   `docs/`: Project documentation (PRD, design documents).
*   `public/`: Contains the static frontend files (e.g., `index.html`).
*   `src/`: Source code.
    *   `functions/`: Firebase Cloud Functions source code (Python).
        *   `venv/`: Python virtual environment for function dependencies.
    *   `scripts/`: Scripts for tasks like data ingestion.
*   `tools/`: Utility scripts (if any).
*   `firebase.json`: Main Firebase project configuration file (at the project root).
*   `.AI-Agentrules`: Contains project-specific learnings and patterns for the AI Agent.
*   `memory-bank/`: Contains contextual documentation for the AI Agent.

### Deployment Steps

1.  **Ensure Firebase Project is Set:**
    *   Log in to Firebase: `firebase login`
    *   Set your active Firebase project: `firebase use --add` (select your project, e.g., `qwiklabs-gcp-00-6d5f50f68707`).
    *   Alternatively, specify the project in deploy commands using `--project <your-project-id>`.

2.  **Set up Python Virtual Environment for Cloud Functions:**
    *   Navigate to the functions source directory: `cd src/functions`
    *   Create a Python virtual environment: `python3.11 -m venv venv`
    *   Activate the virtual environment:
        *   On macOS and Linux: `source venv/bin/activate`
        *   On Windows: `.env\Scripts\activate`
    *   Install dependencies: `pip install -r requirements.txt`
    *   Deactivate (optional): `deactivate`
    *   Return to the project root: `cd ../..`
    *   OR run this whole command in the functions directory : 
        `python3 -m venv venv && . venv/bin/activate && python3 -m pip install -r requirements.txt && firebase deploy --only functions`

3.  **Configure Cloud Function for Unauthenticated Invocation (for MVP):**
    *   The `optimizeProduction` Cloud Function was configured to allow unauthenticated invocations for ease of testing in the MVP. For production, you should secure this endpoint.
    *   This was done using a `gcloud` command (one-time setup):
        ```bash
        gcloud run services add-iam-policy-binding optimizeproduction --member=allUsers --role=roles/run.invoker --region=europe-west1 --project <your-project-id> --platform managed
        ```

4.  **Deploy Firebase Services:**
    *   **Full Deploy (Functions, Firestore rules, Hosting, etc.):**
        ```bash
        firebase deploy --project <your-project-id>
        ```
    *   **Deploy Only Functions (after changes in `src/functions`):
        ```bash
        firebase deploy --only functions --project <your-project-id>
        ```
    *   **Deploy Only Hosting (after changes in `public/`):
        ```bash
        firebase deploy --only hosting --project <your-project-id>
        ```

### Data Ingestion

The `src/scripts/ingest_data.py` script populates Firestore from an Excel file (`data/turning-data.xlsx`).

1.  **Prepare Data Files:**
    *   Ensure `data/turning-data.xlsx` is present and correctly formatted.
    *   Ensure your Firebase service account key JSON is at the project root.
    *   The script uses relative paths: `../../qwiklabs-gcp-00-6d5f50f68707-firebase-adminsdk-fbsvc-9b5f1a76d2.json`, `../../data/turning-data.xlsx`, `../../data/turning-data.csv` (fallback for items).

2.  **Run the Ingestion Script:**
    *   Navigate to the scripts directory: `cd src/scripts`
    *   Set up a local virtual environment for the script (recommended):
        ```bash
        python3.11 -m venv .venv
        source .venv/bin/activate # or .\.venv\Scripts\activate on Windows
        pip install pandas firebase-admin openpyxl
        ```
    *   Run the script:
        ```bash
        python ingest_data.py
        ```
    *   This ingests data into Firestore's `items` and `machines` collections.
    *   Deactivate if needed: `deactivate`
    *   Return to project root: `cd ../..`

### Using the Application

1.  **Access the Frontend:** Open the Firebase Hosting URL for your project (e.g., `https://<your-project-id>.web.app`). The specific URL for this project is `https://qwiklabs-gcp-00-6d5f50f68707.web.app`.
2.  **Run Optimization:** Click the "Run Optimization" button. The process may take a few minutes.
3.  **View Results:**
    *   The frontend will display the JSON response from the optimization function, including costs and the production plan details.
    *   A new document containing these results will also be saved in the `production_plans` collection in your Firestore database, along with a `createdAt` timestamp.

