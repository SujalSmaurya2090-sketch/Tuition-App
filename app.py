from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import gspread
from google.oauth2.service_account import Credentials
import os
from datetime import datetime, timedelta
from functools import wraps
import json

app = Flask(__name__)
app.secret_key = 'tuition_app_secret_key_2026'

app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

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
    error_msg = None
    if request.method == 'POST':
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
                session.permanent = True
                session['logged_in'] = True
                session['email'] = email
                session['username'] = matched_user.get('Full_Name') or email
                role_val = str(matched_user.get('Role', '')).strip().lower()
                session['role'] = 'Teacher' if role_val == 'teacher' else 'Admin'
                return redirect(url_for('home'))
            else:
                error_msg = "Access Denied: Email not registered in Teacher_Master database!"
                
        except Exception as e:
            error_msg = f"Database Error: {str(e)}"

    return render_template('login.html', error=error_msg)

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

@app.route('/get_students/<path:class_name>')
def get_students_by_class(class_name):
    try:
        wks = sheet.worksheet("Students")
        records = wks.get_all_records()
        filtered_students = []
        
        target = str(class_name).replace("Class", "").strip().lower()
        
        for r in records:
            sheet_cls = str(r.get('Class', '')).strip()
            sheet_cls_clean = sheet_cls.replace("Class", "").strip().lower()
            
            if sheet_cls.lower() == str(class_name).strip().lower() or (target and target == sheet_cls_clean) or (target in sheet_cls_clean):
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
        
        now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pay_date = data.get('payment_date') or datetime.now().strftime("%Y-%m-%d")
        
        new_row = [
            fee_id,
            pay_date,
            data.get('student_id', ''),
            data.get('student_name', ''),
            data.get('class_name', ''),
            data.get('amount', ''),
            data.get('for_month', ''),
            data.get('payment_mode', ''),
            session.get('email', 'Staff'),
            now_ts
        ]
        
        wks.append_row(new_row, value_input_option='USER_ENTERED')
        return jsonify({"status": "success", "message": "Fee Recorded Successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/save_attendance', methods=['POST'])
def save_attendance():
    try:
        data = request.get_json(force=True, silent=True) or request.form or {}
        wks = sheet.worksheet("Attendance_Log")
        
        # Multiple keys check karna for old & new JS cache compatibility
        records = (
            data.get('attendance_data') or 
            data.get('records') or 
            data.get('students') or 
            data.get('data') or 
            []
        )
        
        # Agar payload direct array ke roop me aaya ho
        if isinstance(data, list):
            records = data
            
        class_name = data.get('class_name') or data.get('className') or data.get('class') or ''
        att_date = data.get('date') or datetime.now().strftime("%Y-%m-%d")
        marked_by = session.get('email', 'Teacher')
        now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Emergency Fallback: Agar JS ne empty array bheja hai, to Form Data / Raw Parse check karo
        if not records:
            # Check raw json fallback
            try:
                raw_body = json.loads(request.data)
                records = raw_body.get('attendance_data', raw_body.get('records', []))
            except:
                pass

        if not records:
            return jsonify({
                "status": "error", 
                "message": "Payload Empty! Browser Refresh (Ctrl+F5) karke dubara try karein."
            }), 400

        all_vals = [r for r in wks.get_all_values() if any(r)]
        counter = len(all_vals)
        
        rows_to_append = []
        for item in records:
            counter += 1
            log_id = f"ATT-{att_date.replace('-', '')}-{counter:03d}"
            
            s_id = item.get('student_id') or item.get('Student_ID') or item.get('id') or ''
            s_name = item.get('student_name') or item.get('Student_Name') or item.get('name') or ''
            status = item.get('status') or item.get('Status') or 'Present'
            item_cls = item.get('class_name') or item.get('class') or class_name
            
            rows_to_append.append([
                log_id,
                att_date,
                item_cls,
                s_id,
                s_name,
                status,
                marked_by,
                now_ts,
                "Unlocked"
            ])
            
        if rows_to_append:
            wks.append_rows(rows_to_append, value_input_option='USER_ENTERED')
            return jsonify({"status": "success", "message": f"{len(rows_to_append)} Students ki Attendance save ho gayi!"})
        else:
            return jsonify({"status": "error", "message": "No valid rows generated"}), 400

    except Exception as e:
        print("ATTENDANCE ERROR:", str(e))
        return jsonify({"status": "error", "message": str(e)}), 500
        
@app.route('/save_marks', methods=['POST'])
def save_marks():
    try:
        data = request.get_json(force=True, silent=True) or request.form or {}
        wks = sheet.worksheet("Marks_Log")
        
        class_name = data.get('class_name', '')
        subject = data.get('subject', '')
        test_title = data.get('test_title', '')
        test_date = data.get('test_date', datetime.now().strftime("%Y-%m-%d"))
        
        try:
            total_marks = float(data.get('total_marks', 0))
        except:
            total_marks = 0.0
            
        teacher_email = session.get('email', data.get('teacher_email', ''))
        marks_list = data.get('marks_data') or data.get('students') or []
        now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        all_vals = [r for r in wks.get_all_values() if any(r)]
        counter = len(all_vals)
        
        rows_to_append = []
        for m in marks_list:
            counter += 1
            mark_id = f"MRK-{test_date.replace('-', '')}-{counter:03d}"
            
            s_id = m.get('student_id') or m.get('Student_ID') or ''
            s_name = m.get('student_name') or m.get('Student_Name') or ''
            
            try:
                obtained = float(m.get('obtained_marks') or m.get('marks') or 0)
            except:
                obtained = 0.0
                
            percentage = round((obtained / total_marks) * 100, 2) if total_marks > 0 else 0.0
            remarks = m.get('remarks') or ''
            
            rows_to_append.append([
                mark_id,
                test_date,
                class_name,
                subject,
                test_title,
                s_id,
                s_name,
                total_marks,
                obtained,
                f"{percentage}%",
                remarks,
                teacher_email,
                now_ts,
                "Unlocked"
            ])
            
        if rows_to_append:
            wks.append_rows(rows_to_append, value_input_option='USER_ENTERED')
            return jsonify({"status": "success", "message": "Marks Uploaded Successfully!"})
        else:
            return jsonify({"status": "error", "message": "No marks records received!"}), 400

    except Exception as e:
        print("MARKS EXCEPTION:", str(e))
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
