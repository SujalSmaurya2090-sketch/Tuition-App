# ==============================================================================
# FILE: app.py
# PURPOSE: Secure Tuition Management System - AMPLE VISION ACADEMY
# ==============================================================================

from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
from functools import wraps

app = Flask(__name__)
app.secret_key = "AmpleVisionAcademy_Secret_Key_2026#!"

# Google Sheets Connection
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
client = gspread.authorize(creds)
sheet = client.open("Tuition_Master_Database")

# ------------------------------------------------------------------------------
# AUTHENTICATION DECORATORS
# ------------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_email' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_email' not in session or session.get('user_role', '').strip().lower() != 'admin':
            return render_template('unauthorized.html'), 403
        return f(*args, **kwargs)
    return decorated_function

# ------------------------------------------------------------------------------
# ROUTES
# ------------------------------------------------------------------------------

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        try:
            teacher_ws = sheet.worksheet("Teacher_Master")
            records = teacher_ws.get_all_records()
            user = next((r for r in records if str(r.get('Email')).strip().lower() == email and str(r.get('Status')).strip().lower() == 'active'), None)
            
            if user:
                session['user_email'] = email
                session['user_name'] = user.get('Full_Name', 'User')
                session['user_role'] = str(user.get('Role', 'Teacher')).strip().title()
                return redirect(url_for('index'))
            else:
                return render_template('login.html', error="Unauthorized email address or inactive account.")
        except Exception as e:
            return render_template('login.html', error=f"Database Connection Error: {str(e)}")
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

@app.route('/')
@login_required
def index():
    if session.get('user_role') == 'Admin':
        return render_template('dashboard.html', user=session)
    else:
        return render_template('teacher_portal.html', user=session)

@app.route('/attendance')
@login_required
def attendance_page():
    return render_template('attendance.html', user=session)

@app.route('/marks')
@login_required
def marks_page():
    return render_template('marks.html', user=session)

@app.route('/students')
@login_required
def students_page():
    return render_template('students.html', user=session)

@app.route('/fees')
@login_required
def fees_page():
    return render_template('fees.html', user=session)

# ------------------------------------------------------------------------------
# API ENDPOINTS
# ------------------------------------------------------------------------------

@app.route('/get_classes')
@login_required
def get_classes():
    try:
        worksheet = sheet.worksheet("Students_Master")
        records = worksheet.get_all_records()
        classes = sorted(list(set([str(r['Class']).strip() for r in records if r.get('Class')])))
        return jsonify({"status": "success", "classes": classes})
    except Exception as e:
        return jsonify({"status": "error", "classes": [], "message": str(e)})

@app.route('/get_students/<class_name>')
@login_required
def fetch_students(class_name):
    try:
        worksheet = sheet.worksheet("Students_Master")
        records = worksheet.get_all_records()
        students = [
            s for s in records 
            if str(s.get('Class')).strip().lower() == str(class_name).strip().lower() 
            and str(s.get('Status')).strip().lower() == 'active'
        ]
        return jsonify({"status": "success", "students": students})
    except Exception as e:
        return jsonify({"status": "error", "students": [], "message": str(e)})

@app.route('/save_attendance', methods=['POST'])
@login_required
def save_attendance():
    try:
        data = request.json
        class_name = data.get('class_name')
        attendance_list = data.get('attendance')
        teacher_email = session.get('user_email', 'Teacher')
        
        current_time = datetime.now()
        timestamp_str = current_time.strftime("%Y-%m-%d %H:%M:%S")
        date_str = current_time.strftime("%Y-%m-%d")
        
        worksheet = sheet.worksheet("Attendance_Log")
        records = worksheet.get_all_records()
        
        existing_today = [
            r for r in records 
            if str(r.get('Date')).strip() == date_str 
            and str(r.get('Class')).strip().lower() == str(class_name).strip().lower()
        ]
        
        if existing_today:
            return jsonify({
                "status": "warning", 
                "message": f"Attendance for Class '{class_name}' on {date_str} has already been logged."
            }), 400

        rows_to_insert = []
        for item in attendance_list:
            log_id = f"ATT-{date_str}-{item['student_id']}"
            rows_to_insert.append([
                log_id, date_str, class_name, item['student_id'], item['student_name'],
                item['status'], teacher_email, timestamp_str, "Unlocked"
            ])
            
        worksheet.append_rows(rows_to_insert)
        return jsonify({"status": "success", "message": f"Attendance recorded for {len(rows_to_insert)} students!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/save_marks', methods=['POST'])
@login_required
def save_marks():
    try:
        data = request.json
        class_name = data.get('class_name')
        subject = data.get('subject')
        test_title = data.get('test_title')
        test_date = data.get('test_date')
        total_marks = float(data.get('total_marks'))
        marks_list = data.get('marks_data')
        teacher_email = session.get('user_email', 'Teacher')
        
        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        worksheet = sheet.worksheet("Marks_Log")

        rows_to_insert = []
        for item in marks_list:
            obtained = float(item['obtained_marks']) if item['obtained_marks'] != '' else 0.0
            
            if obtained > total_marks:
                return jsonify({"status": "error", "message": f"Obtained marks for {item['student_name']} cannot exceed Total Marks ({total_marks})."}), 400
                
            percentage = round((obtained / total_marks) * 100, 2) if total_marks > 0 else 0.0
            mark_id = f"MRK-{test_date}-{item['student_id']}"
            
            rows_to_insert.append([
                mark_id, test_date, class_name, subject, test_title, item['student_id'],
                item['student_name'], total_marks, obtained, f"{percentage}%",
                item.get('remarks', ''), teacher_email, timestamp_str, "Unlocked"
            ])
            
        worksheet.append_rows(rows_to_insert)
        return jsonify({"status": "success", "message": f"Marks successfully saved for {len(rows_to_insert)} students!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/add_student', methods=['POST'])
@login_required
def add_student():
    try:
        data = request.json
        full_name = data.get('full_name')
        class_name = data.get('class_name')
        parent_contact = data.get('parent_contact')
        monthly_fee = data.get('monthly_fee')
        joining_date = data.get('joining_date')
        
        worksheet = sheet.worksheet("Students_Master")
        existing = worksheet.get_all_records()
        
        next_id_num = len(existing) + 1
        student_id = f"S{next_id_num:02d}"
        
        new_row = [student_id, full_name, class_name, parent_contact, monthly_fee, "Active", joining_date]
        worksheet.append_row(new_row)
        
        return jsonify({"status": "success", "message": f"Student {full_name} ({student_id}) registered successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/save_fee', methods=['POST'])
@login_required
def save_fee():
    try:
        data = request.json
        student_id = data.get('student_id')
        student_name = data.get('student_name')
        class_name = data.get('class_name')
        amount = data.get('amount')
        for_month = data.get('for_month')
        payment_mode = data.get('payment_mode')
        payment_date = data.get('payment_date')
        receipt_no = data.get('receipt_no', '')
        
        # Logged-in user ki email session se retrieve karein
        collected_by = session.get('user_email', 'Admin/Staff')
        
        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        worksheet = sheet.worksheet("Fees_Log")

        fee_log_id = f"FEE-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        # Phase 3 Google Sheet Schema ke mutabiq exact column order:
        # Fee_ID | Payment_Date | Student_ID | Student_Name | Class | Amount_Paid | For_Month | Payment_Mode | Receipt_No | Collected_By | Timestamp
        new_row = [
            fee_log_id, payment_date, student_id, student_name, class_name, 
            amount, for_month, payment_mode, receipt_no, collected_by, timestamp_str
        ]
        
        worksheet.append_row(new_row)
        return jsonify({"status": "success", "message": f"Payment of ₹{amount} logged by {collected_by} for {student_name}!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/dashboard_stats')
@admin_required
def dashboard_stats():
    try:
        st_sheet = sheet.worksheet("Students_Master")
        st_records = st_sheet.get_all_records()
        active_students = [s for s in st_records if str(s.get('Status')).strip().lower() == 'active']
        
        fee_sheet = sheet.worksheet("Fees_Log")
        fee_records = fee_sheet.get_all_records()
        total_collection = sum([float(f.get('Amount_Paid', 0)) for f in fee_records if f.get('Amount_Paid')])
        
        today_str = datetime.now().strftime("%Y-%m-%d")
        att_sheet = sheet.worksheet("Attendance_Log")
        att_records = att_sheet.get_all_records()
        today_att = [a for a in att_records if str(a.get('Date')).strip() == today_str]
        
        present_count = len([a for a in today_att if str(a.get('Status')).strip().lower() == 'present'])
        absent_count = len([a for a in today_att if str(a.get('Status')).strip().lower() == 'absent'])
        
        recent_fees = fee_records[-5:] if len(fee_records) >= 5 else fee_records
        recent_fees.reverse()

        return jsonify({
            "status": "success",
            "active_students": len(active_students),
            "total_collection": total_collection,
            "today_present": present_count,
            "today_absent": absent_count,
            "recent_fees": recent_fees
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)