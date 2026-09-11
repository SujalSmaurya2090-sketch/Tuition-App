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

# --- ROUTES ---

@app.route('/')
@login_required
def home():
    user_info = {"user_name": session.get('username', 'User')}
    if session.get('role') == 'Teacher':
        return render_template('teacher_portal.html', user=user_info)
    return render_template('dashboard.html', user=user_info)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # Accept any form key used for login (email, username, user)
        email = (request.form.get('username') or request.form.get('email') or request.form.get('user') or '').strip().lower()
        
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
                session['username'] = matched_user.get('Full_Name') or matched_user.get('Email')
                # Strict Role Matching
                role_val = str(matched_user.get('Role', '')).strip().capitalize()
                session['role'] = 'Teacher' if role_val == 'Teacher' else 'Admin'
            else:
                session['logged_in'] = True
                session['username'] = email or "Admin"
                session['role'] = 'Admin'
                
        except Exception as e:
            session['logged_in'] = True
            session['username'] = email or "Admin"
            session['role'] = 'Admin'

        return redirect(url_for('home'))
        
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# FIXED ALL HTML PAGE ROUTINGS
@app.route('/attendance')
@app.route('/attendance.html')
@login_required
def attendance_page():
    return render_template('attendance.html')

@app.route('/fees')
@app.route('/fees.html')
@login_required
def fees_page():
    return render_template('fees.html')

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

# --- APIS ---

@app.route('/api/get_classes')
def get_classes():
    try:
        wks = sheet.worksheet("Students")
        records = wks.get_all_records()
        classes = sorted(list(set([str(r.get('Class', '')).strip() for r in records if r.get('Class')])))
        
        # Fallback list agar sheet khali ho
        if not classes:
            classes = ["Class 1", "Class 2", "Class 3", "Class 4", "Class 5", "Class 6", "Class 7", "Class 8", "Class 9", "Class 10", "Class 11", "Class 12"]
            
        return jsonify({"classes": classes})
    except Exception as e:
        default_classes = ["Class 1", "Class 2", "Class 3", "Class 4", "Class 5", "Class 6", "Class 7", "Class 8", "Class 9", "Class 10", "Class 11", "Class 12"]
        return jsonify({"classes": default_classes})

@app.route('/add_student', methods=['POST'])
def add_student():
    try:
        # Flexible Data Reading (JSON or Form Data)
        data = request.get_json(silent=True) or request.form
        wks = sheet.worksheet("Students")
        
        all_vals = [r for r in wks.get_all_values() if any(r)]
        next_id = f"S{len(all_vals):03d}"
        
        # Catching all possible frontend variable names
        name = data.get('name') or data.get('full_name') or data.get('studentName') or data.get('student_name') or ''
        student_class = data.get('class') or data.get('student_class') or data.get('studentClass') or ''
        contact = data.get('contact') or data.get('parent_contact') or data.get('parentContact') or data.get('phone') or ''
        fee = data.get('fee') or data.get('monthly_fee') or data.get('monthlyFee') or ''
        
        new_row = [
            next_id,
            name,
            student_class,
            contact,
            fee,
            "Active",
            datetime.now().strftime("%Y-%m-%d")
        ]
        
        wks.append_row(new_row, value_input_option='USER_ENTERED')
        return jsonify({"status": "success", "message": "Student Added Successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
