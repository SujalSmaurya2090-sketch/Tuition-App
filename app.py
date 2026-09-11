from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import gspread
from google.oauth2.service_account import Credentials
import os
from datetime import datetime
from functools import wraps
import json

app = Flask(__name__)
app.secret_key = 'tuition_app_secret_key_2026'

SCOPE = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

creds_json = os.environ.get("GOOGLE_CREDENTIALS")
if creds_json:
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPE)
else:
    creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPE)

client = gspread.authorize(creds)
sheet = client.open("Tuition_Master_Database")

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- PAGE ROUTES ---

@app.route('/')
@login_required
def home():
    user_info = {"user_name": session.get('username', 'User'), "email": session.get('email', '')}
    if session.get('role') == 'Teacher':
        return render_template('teacher_portal.html', user=user_info)
    return render_template('dashboard.html', user=user_info)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # Matching login.html field: name="email"
        email = (request.form.get('email') or request.form.get('username') or '').strip().lower()
        
        try:
            wks = sheet.worksheet("Teacher_Master")
            records = wks.get_all_records()
            
            matched_user = None
            for row in records:
                if str(row.get('Email', '')).strip().lower() == email:
                    matched_user = row
                    break
            
            if matched_user:
                session['logged_in'] = True
                session['email'] = email
                session['username'] = matched_user.get('Full_Name') or email
                role_val = str(matched_user.get('Role', '')).strip().capitalize()
                session['role'] = 'Teacher' if role_val == 'Teacher' else 'Admin'
            else:
                session['logged_in'] = True
                session['email'] = email
                session['username'] = email or "Admin"
                session['role'] = 'Admin'
                
        except Exception as e:
            session['logged_in'] = True
            session['email'] = email
            session['username'] = email or "Admin"
            session['role'] = 'Admin'

        return redirect(url_for('home'))
        
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/attendance')
@app.route('/attendance.html')
@login_required
def attendance_page():
    return render_template('attendance.html')

@app.route('/fees')
@app.route('/fees.html')
@login_required
def fees_page():
    user_info = {"user_email": session.get('email', session.get('username', 'Staff'))}
    return render_template('fees.html', user=user_info)

@app.route('/marks')
@app.route('/marks.html')
@login_required
def marks_page():
    return render_template('marks.html')

@app.route('/students')
@app.route('/students.html')
@login_required
def students_page():
    return render_template('students.html')

@app.route('/teacher_portal')
@app.route('/teacher_portal.html')
@login_required
def teacher_portal_page():
    user_info = {"user_name": session.get('username', 'Teacher')}
    return render_template('teacher_portal.html', user=user_info)

# --- APIS FOR FRONTEND JS ---

@app.route('/get_classes')
@app.route('/api/get_classes')
def get_classes():
    try:
        wks = sheet.worksheet("Students")
        records = wks.get_all_records()
        classes = sorted(list(set([str(r.get('Class', '')).strip() for r in records if r.get('Class')])))
        if not classes:
            classes = [f"Class {i}" for i in range(1, 13)]
        return jsonify({"classes": classes})
    except Exception as e:
        default_classes = [f"Class {i}" for i in range(1, 13)]
        return jsonify({"classes": default_classes})

@app.route('/get_students/<class_name>')
def get_students_by_class(class_name):
    try:
        wks = sheet.worksheet("Students")
        records = wks.get_all_records()
        filtered_students = []
        
        # Clean target string (e.g. "Class 10th Science" -> "10th science")
        target = str(class_name).replace("Class", "").strip().lower()
        
        for r in records:
            sheet_cls = str(r.get('Class', '')).strip()
            sheet_cls_clean = sheet_cls.replace("Class", "").strip().lower()
            
            # Match exact string, or partial clean match
            if sheet_cls.lower() == str(class_name).strip().lower() or (target and target in sheet_cls_clean):
                filtered_students.append({
                    "Student_ID": str(r.get('Student_ID', '')),
                    "Full_Name": str(r.get('Full_Name', ''))
                })
                
        return jsonify({"students": filtered_students})
    except Exception as e:
        return jsonify({"students": []})

@app.route('/add_student', methods=['POST'])
def add_student():
    try:
        data = request.get_json(silent=True) or request.form
        wks = sheet.worksheet("Students")
        
        all_vals = [r for r in wks.get_all_values() if any(r)]
        next_id = f"S{len(all_vals):03d}"
        
        # Exact payload mapping for students.html JavaScript payload keys
        name = data.get('full_name') or data.get('name') or data.get('studentName') or ''
        student_class = data.get('class_name') or data.get('class') or data.get('student_class') or ''
        contact = data.get('parent_contact') or data.get('contact') or data.get('phone') or ''
        fee = data.get('monthly_fee') or data.get('fee') or ''
        joining_date = data.get('joining_date') or datetime.now().strftime("%Y-%m-%d")
        
        new_row = [
            next_id,
            name,
            student_class,
            contact,
            fee,
            "Active",
            joining_date
        ]
        
        wks.append_row(new_row, value_input_option='USER_ENTERED')
        return jsonify({"status": "success", "message": "Student Added Successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
@app.route('/save_fee', methods=['POST'])
def save_fee():
    try:
        data = request.get_json(silent=True) or request.form
        wks = sheet.worksheet("Fees_Log")
        
        all_vals = [r for r in wks.get_all_values() if any(r)]
        fee_id = f"F{len(all_vals):03d}"
        
        new_row = [
            fee_id,
            data.get('student_id', ''),
            data.get('student_name', ''),
            data.get('class_name', ''),
            data.get('amount', ''),
            data.get('for_month', ''),
            data.get('payment_mode', ''),
            data.get('payment_date', datetime.now().strftime("%Y-%m-%d")),
            data.get('receipt_no', ''),
            session.get('email', 'Staff')
        ]
        
        wks.append_row(new_row, value_input_option='USER_ENTERED')
        return jsonify({"status": "success", "message": "Fee Recorded Successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
