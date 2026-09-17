import os
import json
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session, redirect, url_value, flash
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "ample_vision_academy_secret_key_2026")

# --- Google Sheets Setup ---
# Place service_account.json in the project root or use credentials dictionary
SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

def get_gspread_client():
    creds_file = os.path.join(os.path.dirname(__file__), "service_account.json")
    if os.path.exists(creds_file):
        creds = ServiceAccountCredentials.from_json_keyfile_name(creds_file, SCOPE)
        return gspread.authorize(creds)
    return None

# Static Staff Credentials for Access Control
STAFF_USERS = {
    "admin@amplevision.com": "admin123",
    "staff@amplevision.com": "staff123"
}

# --- Auxiliary Functions ---
def fetch_sheet_records(sheet_name):
    client = get_gspread_client()
    if not client:
        return []
    try:
        sheet = client.open("Ample_Vision_Database").worksheet(sheet_name)
        return sheet.get_all_records()
    except Exception as e:
        print(f"Error fetching sheet {sheet_name}: {e}")
        return []

# --- Authentication Routes ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()

        if email in STAFF_USERS and STAFF_USERS[email] == password:
            session['user_email'] = email
            return redirect('/')
        else:
            return render_template('login.html', error="Invalid email or password.")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# --- Protected Views ---
@app.route('/')
def dashboard():
    if 'user_email' not in session:
        return redirect('/login')
    return render_template('dashboard.html', user_email=session['user_email'])

@app.route('/attendance')
def attendance():
    if 'user_email' not in session:
        return redirect('/login')
    return render_template('attendence.html')

@app.route('/fees')
def fees():
    if 'user_email' not in session:
        return redirect('/login')
    return render_template('fees.html', user={'user_email': session['user_email']})

# --- API Endpoints ---
@app.route('/get_classes')
def get_classes():
    if 'user_email' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    records = fetch_sheet_records("Students")
    if not records:
        # Fallback dummy class list if DB connection unavailable
        classes = ["Class 9", "Class 10", "Class 11 Science", "Class 12 Science"]
    else:
        classes = sorted(list(set(row.get('Class_Name') for row in records if row.get('Class_Name'))))
    
    return jsonify({'classes': classes})

@app.route('/get_students/<class_name>')
def get_students(class_name):
    if 'user_email' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    records = fetch_sheet_records("Students")
    filtered_students = []
    
    for row in records:
        if str(row.get('Class_Name')).strip() == class_name.strip():
            filtered_students.append({
                'Student_ID': str(row.get('Student_ID')),
                'Full_Name': str(row.get('Full_Name'))
            })

    return jsonify({'students': filtered_students})

@app.route('/save_attendance', methods=['POST'])
def save_attendance():
    if 'user_email' not in session:
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

    data = request.json or {}
    class_name = data.get('class_name')
    att_date = data.get('date')
    attendance_data = data.get('attendance_data', [])

    if not class_name or not attendance_data:
        return jsonify({'status': 'error', 'message': 'Invalid submission data'}), 400

    client = get_gspread_client()
    if not client:
        return jsonify({'status': 'error', 'message': 'Database connection failed'}), 500

    try:
        sheet = client.open("Ample_Vision_Database").worksheet("Attendance")
        rows_to_append = []
        for record in attendance_data:
            rows_to_append.append([
                att_date,
                class_name,
                record.get('student_id'),
                record.get('student_name'),
                record.get('status'),
                session['user_email'],
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ])
        sheet.append_rows(rows_to_append)
        return jsonify({'status': 'success', 'message': 'Attendance saved successfully!'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/save_fee', methods=['POST'])
def save_fee():
    if 'user_email' not in session:
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

    data = request.json or {}
    student_id = data.get('student_id')
    student_name = data.get('student_name')
    class_name = data.get('class_name')
    amount = data.get('amount')
    for_month = data.get('for_month')
    payment_mode = data.get('payment_mode')
    payment_date = data.get('payment_date')
    receipt_no = data.get('receipt_no', '')

    client = get_gspread_client()
    if not client:
        return jsonify({'status': 'error', 'message': 'Database connection failed'}), 500

    try:
        sheet = client.open("Ample_Vision_Database").worksheet("Fees")
        sheet.append_row([
            payment_date,
            receipt_no,
            student_id,
            student_name,
            class_name,
            amount,
            for_month,
            payment_mode,
            session['user_email'],
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ])
        return jsonify({'status': 'success', 'message': 'Fee entry recorded successfully!'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
