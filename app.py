from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_from_directory
import gspread
from gspread.exceptions import APIError
from google.oauth2.service_account import Credentials
import os
import time
import json
import threading
from datetime import datetime, timedelta
from functools import wraps
import geopy.distance
import pytz

# --------------------------------------------------
# 1. INITIALIZE FLASK APP
# --------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'tuition_app_secret_key_2026_secure')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

IST = pytz.timezone('Asia/Kolkata')


def get_ist_now():
    return datetime.now(IST)


@app.route('/icon.jpeg')
def serve_icon():
    return send_from_directory('.', 'icon.jpeg')


# --------------------------------------------------
# 2. GOOGLE SHEETS CONNECTION & AUTO-RETRY WRAPPER
# --------------------------------------------------
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds_json = os.environ.get("GOOGLE_CREDENTIALS")
if creds_json:
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPE)
else:
    creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPE)

client = gspread.authorize(creds)
sheet = client.open("Tuition_Master_Database")

# Smart In-Memory Cache (Prevents 429 Too Many Requests)
_SHEETS_CACHE = {}
_WORKSHEET_OBJ_CACHE = {}
_SHEETS_CACHE_LOCK = threading.RLock()
_DEFAULT_CACHE_TTL = 120  # 2 minutes cache (Invalidates automatically on any write)


def _execute_with_retry(func, *args, max_retries=3, **kwargs):
    """Automatically retries Google Sheets API calls if 429 Rate Limit is hit."""
    delay = 1.5
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except APIError as e:
            status_code = getattr(e.response, 'status_code', None)
            if status_code in (429, 500, 502, 503) and attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2  # Exponential backoff: 1.5s -> 3s -> 6s
                continue
            raise
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(1)
                continue
            raise


def _get_worksheet(sheet_name):
    """Caches worksheet metadata objects to save extra API calls."""
    with _SHEETS_CACHE_LOCK:
        if sheet_name in _WORKSHEET_OBJ_CACHE:
            return _WORKSHEET_OBJ_CACHE[sheet_name]
    wks = _execute_with_retry(sheet.worksheet, sheet_name)
    with _SHEETS_CACHE_LOCK:
        _WORKSHEET_OBJ_CACHE[sheet_name] = wks
    return wks


def _cached_sheet_records(sheet_name, ttl=None):
    """Reads worksheet records using cache + 429 auto-retry."""
    ttl = _DEFAULT_CACHE_TTL if ttl is None else ttl
    now = time.time()
    with _SHEETS_CACHE_LOCK:
        cached = _SHEETS_CACHE.get(sheet_name)
        if cached and (now - cached[0] < ttl):
            return [dict(row) for row in cached[1]]

    wks = _get_worksheet(sheet_name)
    records = _execute_with_retry(wks.get_all_records)
    safe_records = [dict(row) for row in records]

    with _SHEETS_CACHE_LOCK:
        _SHEETS_CACHE[sheet_name] = (now, safe_records)
    return [dict(row) for row in safe_records]


def _invalidate_sheet_cache(*sheet_names):
    with _SHEETS_CACHE_LOCK:
        if not sheet_names:
            _SHEETS_CACHE.clear()
        else:
            for name in sheet_names:
                _SHEETS_CACHE.pop(name, None)


def _format_friendly_error(err):
    """Converts technical Python/Google API errors into clean, user-friendly messages."""
    err_str = str(err).lower()
    if isinstance(err, APIError) or '429' in err_str or 'quota' in err_str or 'rate limit' in err_str:
        return "Server par abhi zyada traffic hai (Google Limit). Kripya 10 second ruk kar dubara try karein."
    if 'worksheetnotfound' in err_str:
        return "Database sheet missing hai. Kripya Admin se sampark karein."
    if 'connection' in err_str or 'timeout' in err_str or 'network' in err_str:
        return "Internet ya server connection slow hai. Kripya apna network check karke dubara try karein."
    return "Kuch technical samasya aa gayi hai. Kripya page refresh karke dubara koshish karein."


# --------------------------------------------------
# 3. GLOBAL ERROR HANDLERS (NO RAW TEXT/NUMBERS)
# --------------------------------------------------
@app.errorhandler(404)
def page_not_found(e):
    if request.path.startswith('/api/') or request.is_json:
        return jsonify({"status": "error", "message": "Requested service nahi mili."}), 404
    return redirect(url_for('home'))


@app.errorhandler(500)
def internal_server_error(e):
    if request.path.startswith('/api/') or request.is_json:
        return jsonify({"status": "error", "message": "Server mein temporary dikkat hai. Kripya thodi der baad try karein."}), 500
    return render_template('unauthorized.html'), 500


# --------------------------------------------------
# 4. DATA NORMALIZATION HELPERS
# --------------------------------------------------
def _row_value(row, *aliases, default=''):
    lookup = {str(key).strip().lower(): value for key, value in row.items()}
    for alias in aliases:
        value = lookup.get(str(alias).strip().lower())
        if value is not None and str(value).strip() != '':
            return value
    return default


def _parse_sheet_date(value):
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, 'year') and hasattr(value, 'month') and hasattr(value, 'day'):
        try:
            return value.date() if hasattr(value, 'date') else value
        except Exception:
            pass
    raw = str(value or '').strip()
    if not raw:
        return None
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y', '%Y/%m/%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw.replace('Z', '+00:00')).date()
    except Exception:
        return None


def _normal_key(value):
    return ' '.join(str(value or '').strip().casefold().split())


# --------------------------------------------------
# 5. AUTHENTICATION & PERMISSIONS
# --------------------------------------------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            if request.path.startswith('/api/') or request.is_json:
                return jsonify({"status": "error", "message": "Session expire ho gaya hai. Kripya dubara login karein."}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def role_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'logged_in' not in session:
                if request.path.startswith('/api/') or request.is_json:
                    return jsonify({"status": "error", "message": "Session expire ho gaya hai. Kripya dubara login karein."}), 401
                return redirect(url_for('login'))
            user_role = session.get('role', '').strip()
            if user_role not in allowed_roles:
                if request.path.startswith('/api/') or request.is_json:
                    return jsonify({"status": "error", "message": "Aapko is action ki permission nahi hai."}), 403
                return render_template('unauthorized.html'), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def get_current_teacher():
    email = str(session.get('email', '')).strip().lower()
    if not email:
        return None
    try:
        teachers = _cached_sheet_records("Teacher_Master")
        for row in teachers:
            row_email = str(_row_value(row, 'Email')).strip().lower()
            if row_email == email:
                return row
    except Exception as e:
        print(f"Error in get_current_teacher: {e}")
    return None


def _permission_value_enabled(value):
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() in {'true', 'yes', '1', 'allowed', 'active', 'on'}


def teacher_has_permission(permission_name):
    role = str(session.get('role', '')).strip().lower()
    if role == 'admin':
        return True
    if role != 'teacher':
        return False

    permission_aliases = {
        'fees': 'Collect_Fee',
        'collect_fee': 'Collect_Fee',
        'add_student': 'Add_Student',
        'my_classes': 'My_Classes',
        'attendance': 'Attendance',
        'marks': 'Marks',
        'students': 'Students',
        'scanner': 'Scanner'
    }
    requested = permission_aliases.get(
        str(permission_name or '').strip().lower(),
        str(permission_name or '').strip()
    )

    try:
        teacher = get_current_teacher()
        if not teacher:
            return False

        teacher_id = str(_row_value(teacher, 'Teacher_ID', 'Teacher_Id', 'Teacher_id', 'teacher_id')).strip()
        if not teacher_id:
            return False

        permission_rows = _cached_sheet_records('Teacher_Permissions', ttl=60)
        for row in permission_rows:
            row_id = str(_row_value(row, 'Teacher_ID', 'Teacher_Id', 'Teacher_id', 'teacher_id')).strip()
            if row_id == teacher_id:
                raw = _row_value(row, requested, default=None)
                return _permission_value_enabled(raw)
        return False
    except Exception as e:
        print(f'Permission check error ({requested}): {e}')
        return False


def permission_required(permission_name):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'logged_in' not in session:
                if request.path.startswith('/api/') or request.is_json:
                    return jsonify({"status": "error", "message": "Session expire ho gaya hai. Kripya dubara login karein."}), 401
                return redirect(url_for('login'))
            if not teacher_has_permission(permission_name):
                if request.path.startswith('/api/') or request.is_json:
                    return jsonify({"status": "error", "message": "Aapko is feature ki permission nahi di gayi hai."}), 403
                return render_template('unauthorized.html'), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def permission_required_any(*permission_names):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'logged_in' not in session:
                if request.path.startswith('/api/') or request.is_json:
                    return jsonify({"status": "error", "message": "Session expire ho gaya hai. Kripya dubara login karein."}), 401
                return redirect(url_for('login'))
            if not any(teacher_has_permission(name) for name in permission_names):
                if request.path.startswith('/api/') or request.is_json:
                    return jsonify({"status": "error", "message": "Aapko is data ko dekhne ki permission nahi hai."}), 403
                return render_template('unauthorized.html'), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def _teacher_can_access_class(class_name):
    if session.get('role') == 'Admin':
        return True
    teacher = get_current_teacher()
    if not teacher:
        return False
    teacher_id = str(_row_value(teacher, 'Teacher_ID', 'Teacher_Id', 'Teacher_id')).strip()
    assignments = _cached_sheet_records("Teacher_Assignments")
    for row in assignments:
        row_tid = str(_row_value(row, 'Teacher_Id', 'Teacher_ID', 'Teacher_id')).strip()
        row_cls = str(_row_value(row, 'Class')).strip()
        row_status = str(_row_value(row, 'Status', default='Active')).strip().lower()
        if row_tid == teacher_id and row_cls.lower() == class_name.lower() and row_status == 'active':
            return True
    return False


# --------------------------------------------------
# 6. PAGE ROUTES
# --------------------------------------------------
@app.route('/')
@login_required
def home():
    user_info = {
        "user_name": session.get('username', 'User'),
        "user_email": session.get('email', ''),
        "email": session.get('email', '')
    }
    if session.get('role') == 'Teacher':
        return render_template('teacher_portal.html', user=user_info)
    return render_template('dashboard.html', user=user_info)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('logged_in'):
        return redirect(url_for('home'))

    error_msg = None
    if request.method == 'POST':
        email = (request.form.get('email') or request.form.get('username') or '').strip().lower()
        if not email:
            error_msg = "Kripya apna registered Email ID enter karein."
        else:
            try:
                records = _cached_sheet_records("Teacher_Master", ttl=60)
                matched_user = next(
                    (row for row in records if str(_row_value(row, 'Email')).strip().lower() == email),
                    None
                )

                if matched_user:
                    status_val = str(_row_value(matched_user, 'Status', default='Active')).strip().lower()
                    if status_val not in ('active', ''):
                        error_msg = "Aapka account inactive hai. Kripya Admin se sampark karein."
                    else:
                        session.permanent = True
                        session['logged_in'] = True
                        session['email'] = email
                        session['username'] = str(_row_value(matched_user, 'Full_Name', 'Teacher_Name', default=email)).strip()
                        role_val = str(_row_value(matched_user, 'Role', default='teacher')).strip().lower()
                        session['role'] = 'Teacher' if role_val == 'teacher' else 'Admin'
                        return redirect(url_for('home'))
                else:
                    error_msg = "Yeh Email ID database mein registered nahi hai. Kripya sahi email dalein."
            except Exception as e:
                print(f"Login error: {e}")
                error_msg = _format_friendly_error(e)

    return render_template('login.html', error=error_msg)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/attendance')
@app.route('/attendance.html')
@permission_required('Attendance')
def attendance_page():
    return render_template('attendance.html')


@app.route('/fees')
@app.route('/fees.html')
@permission_required('Fees')
def fees_page():
    user_info = {"user_email": session.get('email', session.get('username', 'Staff'))}
    return render_template('fees.html', user=user_info)


@app.route('/marks')
@app.route('/marks.html')
@permission_required('Marks')
def marks_page():
    return render_template('marks.html')


@app.route('/students')
@app.route('/students.html')
@permission_required('Students')
def students_page():
    return render_template('students.html')


@app.route('/teacher_portal')
@app.route('/teacher_portal.html')
@login_required
def teacher_portal_page():
    user_info = {"user_name": session.get('username', 'Teacher')}
    return render_template('teacher_portal.html', user=user_info)


@app.route('/scan')
@permission_required('Scanner')
def scan_page():
    return render_template('scan.html')


# --------------------------------------------------
# 7. STUDENT, ATTENDANCE, MARKS & FEES APIS
# --------------------------------------------------
@app.route('/get_classes')
@app.route('/api/get_classes')
@permission_required_any('Attendance', 'Marks', 'Students', 'Fees', 'Collect_Fee')
def get_classes():
    try:
        if session.get('role') == 'Admin':
            records = _cached_sheet_records("Students")
            classes = sorted({
                str(_row_value(r, 'Class')).strip()
                for r in records
                if str(_row_value(r, 'Class')).strip()
            })
            return jsonify({"status": "success", "classes": classes})

        teacher = get_current_teacher()
        if not teacher:
            return jsonify({"status": "error", "message": "Teacher profile nahi mili."}), 403

        teacher_id = str(_row_value(teacher, 'Teacher_ID', 'Teacher_Id', 'Teacher_id')).strip()
        assignments = _cached_sheet_records("Teacher_Assignments")

        assigned_classes = sorted({
            str(_row_value(row, 'Class')).strip()
            for row in assignments
            if str(_row_value(row, 'Teacher_Id', 'Teacher_ID', 'Teacher_id')).strip() == teacher_id
            and str(_row_value(row, 'Class')).strip()
            and str(_row_value(row, 'Status', default='Active')).strip().lower() == 'active'
        })

        return jsonify({"status": "success", "classes": assigned_classes})

    except Exception as e:
        print(f"Error in get_classes: {e}")
        return jsonify({"status": "error", "message": _format_friendly_error(e)}), 500


@app.route('/get_students/<path:class_name>')
@permission_required_any('Attendance', 'Marks', 'Students', 'Fees', 'Collect_Fee')
def get_students_by_class(class_name):
    try:
        requested_class = str(class_name).strip()
        if not requested_class:
            return jsonify({"status": "error", "message": "Class select karna zaroori hai."}), 400

        if not _teacher_can_access_class(requested_class):
            return jsonify({"status": "error", "message": "Aapko is class ka access nahi hai."}), 403

        records = _cached_sheet_records("Students")
        filtered_students = []

        for r in records:
            sheet_class = str(_row_value(r, 'Class')).strip()
            status = str(_row_value(r, 'Status', default='Active')).strip().lower()
            if sheet_class.lower() == requested_class.lower() and status == 'active':
                student_id = str(_row_value(r, 'Student_ID', 'Student_Id')).strip()
                student_name = str(_row_value(r, 'Full_Name', 'Student_Name')).strip()
                if student_id:
                    filtered_students.append({
                        "Student_ID": student_id,
                        "Full_Name": student_name
                    })

        return jsonify({"status": "success", "students": filtered_students})

    except Exception as e:
        print(f"Error in get_students_by_class: {e}")
        return jsonify({"status": "error", "message": _format_friendly_error(e)}), 500


@app.route('/add_student', methods=['POST'])
@permission_required('Students')
def add_student():
    try:
        data = request.get_json(silent=True) or request.form
        name = str(data.get('full_name') or data.get('name') or data.get('studentName') or '').strip()
        student_class = str(data.get('class_name') or data.get('class') or data.get('student_class') or '').strip()
        contact = str(data.get('parent_contact') or data.get('contact') or data.get('phone') or '').strip()
        fee = str(data.get('monthly_fee') or data.get('fee') or '').strip()
        joining_date = str(data.get('joining_date') or get_ist_now().strftime("%Y-%m-%d")).strip()

        if not name or not student_class:
            return jsonify({"status": "error", "message": "Student ka naam aur class bharna zaroori hai."}), 400

        existing_records = _cached_sheet_records("Students")
        numbers = []
        for r in existing_records:
            sid = str(_row_value(r, 'Student_ID', 'Student_Id')).strip()
            if sid.upper().startswith('S'):
                try:
                    numbers.append(int(sid[1:]))
                except ValueError:
                    pass
        next_id = f"S{(max(numbers) + 1 if numbers else 1):03d}"

        wks = _get_worksheet("Students")
        new_row = [next_id, name, student_class, contact, fee, "Active", joining_date]
        _execute_with_retry(wks.append_row, new_row, value_input_option='USER_ENTERED')
        _invalidate_sheet_cache('Students')

        return jsonify({"status": "success", "message": f"{name} (ID: {next_id}) successfully add ho gaya hai!"})

    except Exception as e:
        print(f"Error in add_student: {e}")
        return jsonify({"status": "error", "message": _format_friendly_error(e)}), 500


@app.route('/api/students')
@permission_required('Students')
def get_students():
    try:
        records = _cached_sheet_records("Students")
        students = []
        for row in records:
            student_id = str(_row_value(row, 'Student_ID', 'Student_Id')).strip()
            if not student_id:
                continue
            students.append({
                'id': student_id,
                'name': str(_row_value(row, 'Full_Name', 'Student_Name')).strip(),
                'class': str(_row_value(row, 'Class')).strip(),
                'contact': str(_row_value(row, 'Parent_Contact', 'Phone')).strip(),
                'fee': str(_row_value(row, 'Monthly_Fee', 'Fee')).strip(),
                'status': str(_row_value(row, 'Status', default='Active')).strip(),
                'joining_date': str(_row_value(row, 'Joining_Date')).strip()
            })

        return jsonify({'status': 'success', 'students': students})

    except Exception as e:
        print(f"Error in get_students: {e}")
        return jsonify({'status': 'error', 'message': _format_friendly_error(e)}), 500


@app.route('/save_fee', methods=['POST'])
@permission_required('Fees')
def save_fee():
    try:
        data = request.get_json(silent=True) or request.form
        student_id = str(data.get('student_id', '')).strip()
        student_name = str(data.get('student_name', '')).strip()
        class_name = str(data.get('class_name', '')).strip()
        amount = str(data.get('amount', '')).strip()
        for_month = str(data.get('for_month', '')).strip()
        payment_mode = str(data.get('payment_mode', 'Cash')).strip()

        if not student_id or not amount:
            return jsonify({"status": "error", "message": "Student aur Fee Amount select karna zaroori hai."}), 400

        existing_fees = _cached_sheet_records("Fees_Log")
        numbers = []
        for r in existing_fees:
            fid = str(_row_value(r, 'Fee_ID', 'Fee_Id')).strip()
            if fid.upper().startswith('F'):
                try:
                    numbers.append(int(fid[1:]))
                except ValueError:
                    pass
        fee_id = f"F{(max(numbers) + 1 if numbers else 1):03d}"

        now_ist = get_ist_now()
        now_ts = now_ist.strftime("%Y-%m-%d %H:%M:%S")
        pay_date = str(data.get('payment_date') or now_ist.strftime("%Y-%m-%d")).strip()

        wks = _get_worksheet("Fees_Log")
        new_row = [
            fee_id, pay_date, student_id, student_name,
            class_name, amount, for_month, payment_mode,
            session.get('email', 'Staff'), now_ts
        ]
        _execute_with_retry(wks.append_row, new_row, value_input_option='USER_ENTERED')
        _invalidate_sheet_cache('Fees_Log')

        return jsonify({"status": "success", "message": f"₹{amount} ki fee entry ({student_name}) save ho gayi hai!"})

    except Exception as e:
        print(f"Error in save_fee: {e}")
        return jsonify({"status": "error", "message": _format_friendly_error(e)}), 500


@app.route('/save_attendance', methods=['POST'])
@permission_required('Attendance')
def save_attendance():
    try:
        data = request.get_json(silent=True) or request.form or {}
        if not isinstance(data, dict):
            return jsonify({"status": "error", "message": "Invalid attendance data mila hai."}), 400

        class_name = str(data.get('class_name') or data.get('className') or data.get('class') or '').strip()
        att_date = str(data.get('date') or get_ist_now().strftime("%Y-%m-%d")).strip()
        records = data.get('attendance_data') or data.get('records') or data.get('students') or data.get('data') or []

        if not class_name:
            return jsonify({"status": "error", "message": "Class select karna zaroori hai."}), 400
        if not records or not isinstance(records, list):
            return jsonify({"status": "error", "message": "Koi attendance record select nahi kiya gaya."}), 400

        try:
            datetime.strptime(att_date, "%Y-%m-%d")
        except ValueError:
            return jsonify({"status": "error", "message": "Date format galat hai (YYYY-MM-DD hona chahiye)."}), 400

        if not _teacher_can_access_class(class_name):
            return jsonify({"status": "error", "message": "Aapko is class ki attendance lene ki permission nahi hai."}), 403

        student_records = _cached_sheet_records("Students")
        valid_students = {}
        for row in student_records:
            row_class = str(_row_value(row, 'Class')).strip()
            row_status = str(_row_value(row, 'Status', default='Active')).strip().lower()
            student_id = str(_row_value(row, 'Student_ID', 'Student_Id')).strip()
            student_name = str(_row_value(row, 'Full_Name', 'Student_Name')).strip()
            if row_class.lower() == class_name.lower() and row_status == 'active' and student_id:
                valid_students[student_id] = {"name": student_name, "class": row_class}

        clean_records = []
        seen_student_ids = set()

        for item in records:
            if not isinstance(item, dict):
                return jsonify({"status": "error", "message": "Student record format galat hai."}), 400

            student_id = str(item.get('student_id') or item.get('Student_ID') or item.get('id') or '').strip()
            status = str(item.get('status') or item.get('Status') or 'Present').strip().title()

            if student_id not in valid_students:
                return jsonify({"status": "error", "message": f"Student ID {student_id} is class mein active nahi hai."}), 400
            if student_id in seen_student_ids:
                return jsonify({"status": "error", "message": f"Duplicate entry mili hai ({student_id})."}), 400
            if status not in ['Present', 'Absent']:
                return jsonify({"status": "error", "message": f"Invalid status ({status}) mila hai."}), 400

            seen_student_ids.add(student_id)
            clean_records.append({
                "student_id": student_id,
                "student_name": valid_students[student_id]["name"],
                "status": status
            })

        existing_records = _cached_sheet_records("Attendance_Log")
        existing_keys = set()
        for row in existing_records:
            parsed_d = _parse_sheet_date(_row_value(row, 'Date'))
            existing_date = parsed_d.strftime("%Y-%m-%d") if parsed_d else str(_row_value(row, 'Date')).strip()
            existing_class = str(_row_value(row, 'Class')).strip().lower()
            existing_sid = str(_row_value(row, 'Student_ID', 'Student_Id')).strip()
            if existing_date == att_date and existing_class == class_name.lower() and existing_sid:
                existing_keys.add((existing_date, existing_class, existing_sid))

        duplicate_students = [
            item["student_name"] for item in clean_records
            if (att_date, class_name.lower(), item["student_id"]) in existing_keys
        ]

        if duplicate_students:
            return jsonify({
                "status": "error",
                "message": f"{att_date} ki attendance {class_name} ke liye pehle se mark ho chuki hai ({', '.join(duplicate_students[:5])})."
            }), 409

        marked_by = session.get('email', 'Teacher')
        now_ts = get_ist_now().strftime("%Y-%m-%d %H:%M:%S")
        counter = len(existing_records) + 1
        rows_to_append = []

        for item in clean_records:
            counter += 1
            log_id = f"ATT-{att_date.replace('-', '')}-{counter:03d}"
            rows_to_append.append([
                log_id, att_date, class_name,
                item["student_id"], item["student_name"],
                item["status"], marked_by, now_ts, "Locked"
            ])

        attendance_sheet = _get_worksheet("Attendance_Log")
        _execute_with_retry(attendance_sheet.append_rows, rows_to_append, value_input_option='USER_ENTERED')
        _invalidate_sheet_cache('Attendance_Log')

        return jsonify({
            "status": "success",
            "message": f"{len(rows_to_append)} students ki attendance successfully save ho gayi!"
        })

    except Exception as e:
        print(f"Error in save_attendance: {e}")
        return jsonify({"status": "error", "message": _format_friendly_error(e)}), 500


@app.route('/save_marks', methods=['POST'])
@permission_required('Marks')
def save_marks():
    try:
        data = request.get_json(force=True, silent=True) or request.form or {}
        class_name = str(data.get('class_name', '')).strip()
        subject = str(data.get('subject', '')).strip()
        test_title = str(data.get('test_title', '')).strip()
        test_date = str(data.get('test_date') or get_ist_now().strftime("%Y-%m-%d")).strip()

        if not class_name or not subject or not test_title:
            return jsonify({"status": "error", "message": "Class, Subject aur Test Title bharna zaroori hai."}), 400

        if not _teacher_can_access_class(class_name):
            return jsonify({"status": "error", "message": "Aapko is class ke marks upload karne ki permission nahi hai."}), 403

        try:
            total_marks = float(data.get('total_marks', 0))
        except ValueError:
            total_marks = 0.0

        teacher_email = session.get('email', data.get('teacher_email', ''))
        marks_list = data.get('marks_data') or data.get('students') or []
        now_ts = get_ist_now().strftime("%Y-%m-%d %H:%M:%S")

        existing_marks = _cached_sheet_records("Marks_Log")
        counter = len(existing_marks) + 1
        rows_to_append = []

        for m in marks_list:
            counter += 1
            mark_id = f"MRK-{test_date.replace('-', '')}-{counter:03d}"
            s_id = str(m.get('student_id') or m.get('Student_ID') or '').strip()
            s_name = str(m.get('student_name') or m.get('Student_Name') or '').strip()

            try:
                obtained = float(m.get('obtained_marks') or m.get('marks') or 0)
            except ValueError:
                obtained = 0.0

            percentage = round((obtained / total_marks) * 100, 2) if total_marks > 0 else 0.0
            remarks = str(m.get('remarks') or '').strip()

            rows_to_append.append([
                mark_id, test_date, class_name, subject, test_title,
                s_id, s_name, total_marks, obtained, f"{percentage}%",
                remarks, teacher_email, now_ts, "Unlocked"
            ])

        if not rows_to_append:
            return jsonify({"status": "error", "message": "Koi marks data receive nahi hua!"}), 400

        wks = _get_worksheet("Marks_Log")
        _execute_with_retry(wks.append_rows, rows_to_append, value_input_option='USER_ENTERED')
        _invalidate_sheet_cache('Marks_Log')

        return jsonify({"status": "success", "message": f"{len(rows_to_append)} students ke marks successfully upload ho gaye!"})

    except Exception as e:
        print(f"Error in save_marks: {e}")
        return jsonify({"status": "error", "message": _format_friendly_error(e)}), 500


# --------------------------------------------------
# 8. ADMIN DASHBOARD, PERMISSIONS & ASSIGNMENTS
# --------------------------------------------------
@app.route('/api/admin_summary', methods=['GET', 'POST'])
@role_required('Admin')
def admin_summary():
    try:
        def get_or_create_assignment_sheet():
            try:
                return _get_worksheet("Teacher_Assignments")
            except Exception:
                wks = _execute_with_retry(sheet.add_worksheet, title="Teacher_Assignments", rows=1000, cols=8)
                _execute_with_retry(wks.append_row, [
                    "Assignment_ID", "Teacher_Id", "Teacher_Name",
                    "Branch", "Class", "Medium", "Subject", "Status"
                ], value_input_option='USER_ENTERED')
                _WORKSHEET_OBJ_CACHE["Teacher_Assignments"] = wks
                return wks

        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            if data.get('action') != 'save_assignment':
                return jsonify({'status': 'error', 'message': 'Invalid admin action.'}), 400

            teacher_id = str(data.get('teacher_id', '')).strip()
            teacher_name = str(data.get('teacher_name', '')).strip()
            branch = str(data.get('branch', '')).strip() or 'Main'
            class_name = str(data.get('class_name', '')).strip()
            medium = str(data.get('medium', '')).strip()
            subject = str(data.get('subject', '')).strip()

            if not teacher_id or not class_name or not subject:
                return jsonify({'status': 'error', 'message': 'Teacher, Class aur Subject select karna zaroori hai.'}), 400

            assignment_sheet = get_or_create_assignment_sheet()
            records = _cached_sheet_records("Teacher_Assignments")

            for row in records:
                if (
                    _normal_key(_row_value(row, 'Teacher_Id', 'Teacher_ID', 'Teacher_id')) == _normal_key(teacher_id)
                    and _normal_key(_row_value(row, 'Class')) == _normal_key(class_name)
                    and _normal_key(_row_value(row, 'Subject')) == _normal_key(subject)
                    and _normal_key(_row_value(row, 'Branch', default='Main')) == _normal_key(branch)
                    and _normal_key(_row_value(row, 'Medium')) == _normal_key(medium)
                    and _normal_key(_row_value(row, 'Status', default='Active')) == 'active'
                ):
                    return jsonify({'status': 'error', 'message': 'Yeh teacher pehle se is class aur subject ke liye assigned hai.'}), 409

            numbers = []
            for row in records:
                aid = str(_row_value(row, 'Assignment_ID')).strip()
                if aid.upper().startswith('A'):
                    try:
                        numbers.append(int(aid[1:]))
                    except ValueError:
                        pass

            assignment_id = f"A{(max(numbers) + 1 if numbers else 1):03d}"
            _execute_with_retry(assignment_sheet.append_row, [
                assignment_id, teacher_id, teacher_name, branch, class_name, medium, subject, 'Active'
            ], value_input_option='USER_ENTERED')
            _invalidate_sheet_cache('Teacher_Assignments')

            return jsonify({
                'status': 'success',
                'message': 'Teacher assignment successfully save ho gaya!',
                'assignment_id': assignment_id
            })

        # GET: Load Dashboard Data (All reads use cache!)
        att_records = _cached_sheet_records("Attendance_Log")
        marks_records = _cached_sheet_records("Marks_Log")
        fees_records = _cached_sheet_records("Fees_Log")
        teacher_records = _cached_sheet_records("Teacher_Master")
        student_records = _cached_sheet_records("Students")

        teachers = []
        for row in teacher_records:
            t_id = str(_row_value(row, 'Teacher_ID', 'Teacher_Id', 'Teacher_id')).strip()
            t_name = str(_row_value(row, 'Teacher_Name', 'Full_Name', 'Name')).strip()
            if t_id and t_name:
                teachers.append({
                    'teacher_id': t_id,
                    'teacher_name': t_name,
                    'email': str(_row_value(row, 'Email')).strip(),
                    'status': str(_row_value(row, 'Status', default='Active')).strip()
                })

        classes = sorted({
            str(_row_value(row, 'Class')).strip()
            for row in student_records
            if str(_row_value(row, 'Class')).strip()
        })

        assignments = []
        try:
            for row in _cached_sheet_records("Teacher_Assignments"):
                assignments.append({
                    'assignment_id': str(_row_value(row, 'Assignment_ID')).strip(),
                    'teacher_id': str(_row_value(row, 'Teacher_Id', 'Teacher_ID', 'Teacher_id')).strip(),
                    'teacher_name': str(_row_value(row, 'Teacher_Name', 'Full_Name')).strip(),
                    'branch': str(_row_value(row, 'Branch')).strip(),
                    'class': str(_row_value(row, 'Class')).strip(),
                    'medium': str(_row_value(row, 'Medium')).strip(),
                    'subject': str(_row_value(row, 'Subject')).strip(),
                    'status': str(_row_value(row, 'Status')).strip()
                })
        except Exception as e:
            print(f"[ADMIN SUMMARY] Assignment load warning: {e}")

        today = get_ist_now().date()
        student_by_id = {}
        student_by_name_class = {}
        for student in student_records:
            sid = str(_row_value(student, 'Student_ID', 'Student_Id')).strip()
            sname = str(_row_value(student, 'Full_Name', 'Student_Name', 'Name')).strip()
            sclass = str(_row_value(student, 'Class', 'Batch')).strip()
            contact = str(_row_value(student, 'Parent_Contact', 'Parent_Phone', 'Phone', 'Mobile')).strip()
            item = {'student_id': sid, 'name': sname, 'class': sclass, 'contact': contact}
            if sid:
                student_by_id[sid] = item
            if sname:
                student_by_name_class[(_normal_key(sname), _normal_key(sclass))] = item

        attendance_by_student = {}
        for row in att_records:
            row_date = _parse_sheet_date(_row_value(row, 'Date', 'Attendance_Date'))
            if row_date != today:
                continue
            status = _normal_key(_row_value(row, 'Status', 'Attendance_Status'))
            if status not in ('present', 'absent'):
                continue
            sid = str(_row_value(row, 'Student_ID', 'Student_Id')).strip()
            name = str(_row_value(row, 'Student_Name', 'Full_Name', 'Name')).strip()
            c_name = str(_row_value(row, 'Class', 'Batch')).strip()
            identity = (sid or _normal_key(name), _normal_key(c_name))
            attendance_by_student[identity] = {'status': status, 'student_id': sid, 'name': name, 'class': c_name}

        today_present = sum(1 for r in attendance_by_student.values() if r['status'] == 'present')
        today_absent = sum(1 for r in attendance_by_student.values() if r['status'] == 'absent')
        absent_list = []
        for record in attendance_by_student.values():
            if record['status'] != 'absent':
                continue
            student = student_by_id.get(record['student_id']) or student_by_name_class.get((_normal_key(record['name']), _normal_key(record['class'])))
            absent_list.append({
                'student_id': record['student_id'] or (student or {}).get('student_id', ''),
                'name': record['name'] or (student or {}).get('name', ''),
                'class': record['class'] or (student or {}).get('class', ''),
                'contact': (student or {}).get('contact', '')
            })
        absent_list.sort(key=lambda item: (_normal_key(item.get('class')), _normal_key(item.get('name'))))

        recent_marks = [{
            'date': _row_value(row, 'Date'),
            'class': _row_value(row, 'Class'),
            'subject': _row_value(row, 'Subject'),
            'test_title': _row_value(row, 'Test_Title'),
            'name': _row_value(row, 'Student_Name'),
            'obtained': _row_value(row, 'Obtained_Marks'),
            'total': _row_value(row, 'Total_Marks')
        } for row in marks_records[-10:]]

        monthly_collection = 0.0
        month_start = today.replace(day=1)
        for row in fees_records:
            raw_amount = str(_row_value(row, 'Amount_Paid', 'Amount', 'Paid_Amount', default=0) or 0)
            try:
                amount = float(raw_amount.replace('₹', '').replace(',', '').strip() or 0)
            except ValueError:
                amount = 0.0
            payment_date = _parse_sheet_date(_row_value(row, 'Payment_Date', 'Date', 'Paid_Date'))
            if payment_date and month_start <= payment_date <= today:
                monthly_collection += amount

        recent_fees = [{
            'date': _row_value(row, 'Payment_Date', 'Date'),
            'name': _row_value(row, 'Student_Name'),
            'class': _row_value(row, 'Class'),
            'amount': _row_value(row, 'Amount_Paid', 'Amount'),
            'mode': _row_value(row, 'Payment_Mode')
        } for row in fees_records[-10:]]

        return jsonify({
            'status': 'success',
            'today_present': today_present,
            'today_absent': today_absent,
            'monthly_collection': monthly_collection,
            'collection_month': today.strftime('%B %Y'),
            'absent_students': absent_list,
            'recent_marks': list(reversed(recent_marks)),
            'recent_fees': list(reversed(recent_fees)),
            'teachers': teachers,
            'classes': classes,
            'assignments': assignments
        })

    except Exception as e:
        print(f"[ADMIN SUMMARY ERROR] {e}")
        return jsonify({'status': 'error', 'message': _format_friendly_error(e)}), 500


@app.route('/api/get_teacher_permissions')
@role_required('Admin')
def get_teacher_permissions():
    try:
        teacher_id = str(request.args.get('teacher_id', '')).strip()
        if not teacher_id:
            return jsonify({'status': 'error', 'message': 'Teacher ID zaroori hai.'}), 400

        records = _cached_sheet_records('Teacher_Permissions', ttl=60)
        default_permissions = {
            'Attendance': False, 'Marks': False, 'Add_Student': False,
            'Collect_Fee': False, 'Students': False, 'My_Classes': False
        }

        for row in records:
            row_teacher_id = str(_row_value(row, 'Teacher_ID', 'Teacher_Id', 'Teacher_id')).strip()
            if row_teacher_id == teacher_id:
                permissions = {
                    name: _permission_value_enabled(_row_value(row, name))
                    for name in default_permissions
                }
                return jsonify({'status': 'success', 'permissions': permissions})

        return jsonify({'status': 'success', 'permissions': default_permissions})

    except Exception as e:
        print(f"Error in get_teacher_permissions: {e}")
        return jsonify({'status': 'error', 'message': _format_friendly_error(e)}), 500


@app.route('/api/save_teacher_permissions', methods=['POST'])
@role_required('Admin')
def save_teacher_permissions():
    try:
        data = request.get_json(silent=True) or {}
        teacher_id = str(data.get('teacher_id', '')).strip()
        permissions = data.get('permissions', {})

        if not teacher_id:
            return jsonify({'status': 'error', 'message': 'Teacher ID zaroori hai.'}), 400

        permissions_sheet = _get_worksheet("Teacher_Permissions")
        records = _cached_sheet_records('Teacher_Permissions', ttl=30)

        existing_row = None
        for index, row in enumerate(records, start=2):
            row_teacher_id = str(_row_value(row, 'Teacher_ID', 'Teacher_Id', 'Teacher_id')).strip()
            if row_teacher_id == teacher_id:
                existing_row = index
                break

        values = [
            teacher_id,
            _permission_value_enabled(permissions.get('Attendance', False)),
            _permission_value_enabled(permissions.get('Marks', False)),
            _permission_value_enabled(permissions.get('Add_Student', False)),
            _permission_value_enabled(permissions.get('Collect_Fee', False)),
            _permission_value_enabled(permissions.get('Students', False)),
            _permission_value_enabled(permissions.get('My_Classes', False))
        ]

        if existing_row:
            _execute_with_retry(permissions_sheet.update, f'A{existing_row}:G{existing_row}', [values], value_input_option='USER_ENTERED')
        else:
            _execute_with_retry(permissions_sheet.append_row, values, value_input_option='USER_ENTERED')

        _invalidate_sheet_cache('Teacher_Permissions')
        return jsonify({'status': 'success', 'message': 'Teacher permissions successfully save ho gayi!'})

    except Exception as e:
        print(f"Error in save_teacher_permissions: {e}")
        return jsonify({'status': 'error', 'message': _format_friendly_error(e)}), 500


# --------------------------------------------------
# 9. TEACHER ATTENDANCE & QR SCANNER
# --------------------------------------------------
@app.route('/api/get_teacher_attendance')
@login_required
def get_teacher_attendance():
    try:
        teacher_records = _cached_sheet_records("Teacher_Master")
        log_records = _cached_sheet_records("Teacher_Attendance_Log")
        today_date = get_ist_now().strftime('%Y-%m-%d')

        today_attendance = {}
        for log_row in log_records:
            parsed_d = _parse_sheet_date(_row_value(log_row, 'Date'))
            log_date = parsed_d.strftime('%Y-%m-%d') if parsed_d else str(_row_value(log_row, 'Date')).strip()
            log_tid = str(_row_value(log_row, 'Teacher_ID', 'Teacher_Id', 'Teacher_id')).strip()
            if log_date == today_date and log_tid:
                today_attendance[log_tid] = str(_row_value(log_row, 'Status', default='Present')).strip()

        teachers = []
        for index, row in enumerate(teacher_records, start=2):
            t_id = str(_row_value(row, 'Teacher_ID', 'Teacher_Id', 'Teacher_id')).strip()
            t_name = str(_row_value(row, 'Full_Name', 'Teacher_Name')).strip()
            if t_id and t_name:
                teachers.append({
                    'row_id': index,
                    'id': t_id,
                    'name': t_name,
                    'role': str(_row_value(row, 'Role')).strip(),
                    'status': today_attendance.get(t_id, 'Absent')
                })

        return jsonify({'status': 'success', 'teachers': teachers})

    except Exception as e:
        print(f"Error in get_teacher_attendance: {e}")
        return jsonify({'status': 'error', 'message': _format_friendly_error(e)}), 500


@app.route('/api/mark_teacher_attendance', methods=['POST'])
@role_required('Admin')
def mark_teacher_attendance():
    try:
        data = request.get_json(silent=True) or {}
        row_id = data.get('row_id')
        status = str(data.get('status', '')).strip().title()

        if not row_id or status not in ['Present', 'Absent']:
            return jsonify({'status': 'error', 'message': 'Invalid teacher record ya status.'}), 400

        teacher_records = _cached_sheet_records("Teacher_Master")
        idx_in_list = int(row_id) - 2
        if idx_in_list < 0 or idx_in_list >= len(teacher_records):
            return jsonify({'status': 'error', 'message': 'Teacher record nahi mila.'}), 404

        teacher_row = teacher_records[idx_in_list]
        t_id = str(_row_value(teacher_row, 'Teacher_ID', 'Teacher_Id', 'Teacher_id')).strip()
        t_name = str(_row_value(teacher_row, 'Full_Name', 'Teacher_Name')).strip()

        now_ist = get_ist_now()
        today_date = now_ist.strftime('%Y-%m-%d')
        current_time = now_ist.strftime('%I:%M:%S %p')

        log_sheet = _get_worksheet("Teacher_Attendance_Log")
        log_records = _cached_sheet_records("Teacher_Attendance_Log")

        entry_found = False
        for idx, row in enumerate(log_records, start=2):
            parsed_d = _parse_sheet_date(_row_value(row, 'Date'))
            sheet_date = parsed_d.strftime('%Y-%m-%d') if parsed_d else str(_row_value(row, 'Date')).strip()
            sheet_tid = str(_row_value(row, 'Teacher_ID', 'Teacher_Id', 'Teacher_id')).strip()
            if sheet_date == today_date and sheet_tid == t_id:
                _execute_with_retry(log_sheet.update_cell, idx, 5, status)
                entry_found = True
                break

        if not entry_found:
            _execute_with_retry(log_sheet.append_row, [today_date, t_id, t_name, current_time, status], value_input_option='USER_ENTERED')

        _invalidate_sheet_cache('Teacher_Attendance_Log')
        return jsonify({
            'status': 'success',
            'message': f'{t_name} ki attendance {status} mark ho gayi.',
            'teacher_id': t_id,
            'teacher_name': t_name,
            'attendance_status': status,
            'date': today_date
        })

    except Exception as e:
        print(f"Error in mark_teacher_attendance: {e}")
        return jsonify({'status': 'error', 'message': _format_friendly_error(e)}), 500


@app.route('/api/teacher/my_classes')
@permission_required('My_Classes')
def teacher_my_classes():
    try:
        teacher_info = get_current_teacher()
        if not teacher_info:
            return jsonify({'status': 'error', 'message': 'Teacher profile nahi mili.'}), 404

        teacher_id = str(_row_value(teacher_info, 'Teacher_ID', 'Teacher_Id', 'Teacher_id')).strip()
        assignments = _cached_sheet_records("Teacher_Assignments")

        my_classes = []
        for row in assignments:
            row_tid = str(_row_value(row, 'Teacher_Id', 'Teacher_ID', 'Teacher_id')).strip()
            status = str(_row_value(row, 'Status', default='Active')).strip().lower()
            if row_tid == teacher_id and status == 'active':
                my_classes.append({
                    'assignment_id': str(_row_value(row, 'Assignment_ID')).strip(),
                    'branch': str(_row_value(row, 'Branch')).strip(),
                    'class': str(_row_value(row, 'Class')).strip(),
                    'medium': str(_row_value(row, 'Medium')).strip(),
                    'subject': str(_row_value(row, 'Subject')).strip()
                })

        return jsonify({
            'status': 'success',
            'teacher_id': teacher_id,
            'teacher_name': str(_row_value(teacher_info, 'Full_Name', 'Teacher_Name')).strip(),
            'classes': my_classes
        })

    except Exception as e:
        print(f"Error in teacher_my_classes: {e}")
        return jsonify({'status': 'error', 'message': _format_friendly_error(e)}), 500


BRANCHES = [
    {"name": "Branch 1", "lat": 22.9984656, "lon": 72.6409959},
    {"name": "Branch 2", "lat": 23.0010259, "lon": 72.6266719}
]


@app.route('/api/scan_qr_attendance', methods=['POST'])
@permission_required('Scanner')
def scan_qr_attendance():
    try:
        data = request.get_json(silent=True) or {}
        user_email = str(session.get('email') or data.get('email') or '').strip().lower()
        if not user_email:
            return jsonify({'status': 'error', 'message': 'Kripya pehle login karein.'}), 401

        user_lat = data.get('lat')
        user_lon = data.get('lon')
        gps_accuracy = data.get('accuracy')

        if user_lat is None or user_lon is None:
            return jsonify({'status': 'error', 'message': 'Mobile location (GPS) on karke allow kijiye!'}), 400

        try:
            user_lat = float(user_lat)
            user_lon = float(user_lon)
            if gps_accuracy is not None:
                gps_accuracy = float(gps_accuracy)
        except (TypeError, ValueError):
            return jsonify({'status': 'error', 'message': 'Invalid GPS coordinates mile hain.'}), 400

        user_loc = (user_lat, user_lon)
        valid_branch = False
        nearest_distance = None

        for branch in BRANCHES:
            branch_loc = (float(branch['lat']), float(branch['lon']))
            distance = geopy.distance.geodesic(branch_loc, user_loc).meters
            if nearest_distance is None or distance < nearest_distance:
                nearest_distance = distance
            if distance <= 60 and (gps_accuracy is None or gps_accuracy <= 65):
                valid_branch = True
                break

        if not valid_branch:
            dist_txt = f"{nearest_distance:.0f}m" if nearest_distance is not None else "unknown"
            return jsonify({
                'status': 'error',
                'message': f'Aap tuition branch se approx {dist_txt} door hain. Branch ke paas jaakar dubara scan karein.'
            }), 400

        now_ist = get_ist_now()
        today_date = now_ist.strftime('%Y-%m-%d')
        current_time = now_ist.strftime('%I:%M:%S %p')

        teacher_info = get_current_teacher()
        if not teacher_info:
            return jsonify({'status': 'error', 'message': 'Aapki Email Teacher Database mein nahi mili!'}), 403

        t_id = str(_row_value(teacher_info, 'Teacher_ID', 'Teacher_Id', 'Teacher_id')).strip()
        t_name = str(_row_value(teacher_info, 'Full_Name', 'Teacher_Name')).strip()

        log_records = _cached_sheet_records("Teacher_Attendance_Log")
        for row in log_records:
            parsed_d = _parse_sheet_date(_row_value(row, 'Date'))
            sheet_date = parsed_d.strftime('%Y-%m-%d') if parsed_d else str(_row_value(row, 'Date')).strip()
            sheet_tid = str(_row_value(row, 'Teacher_ID', 'Teacher_Id', 'Teacher_id')).strip()
            if sheet_date == today_date and sheet_tid == t_id:
                return jsonify({
                    'status': 'success',
                    'already_marked': True,
                    'message': f'{t_name}, aapki aaj ki attendance pehle se Present mark hai.'
                })

        log_sheet = _get_worksheet("Teacher_Attendance_Log")
        _execute_with_retry(log_sheet.append_row, [today_date, t_id, t_name, current_time, 'Present'], value_input_option='USER_ENTERED')
        _invalidate_sheet_cache('Teacher_Attendance_Log')

        return jsonify({
            'status': 'success',
            'already_marked': False,
            'message': f'Attendance successfully Present mark ho gayi ({t_name})!'
        })

    except Exception as e:
        print(f"Error in scan_qr_attendance: {e}")
        return jsonify({'status': 'error', 'message': _format_friendly_error(e)}), 500


@app.route('/api/admin/teachers')
@role_required('Admin')
def admin_get_teachers():
    try:
        records = _cached_sheet_records("Teacher_Master")
        teachers = []
        for row in records:
            t_id = str(_row_value(row, 'Teacher_ID', 'Teacher_Id', 'Teacher_id')).strip()
            t_name = str(_row_value(row, 'Teacher_Name', 'Full_Name', 'Name')).strip()
            if t_id and t_name:
                teachers.append({
                    "teacher_id": t_id,
                    "teacher_name": t_name,
                    "email": str(_row_value(row, 'Email')).strip(),
                    "status": str(_row_value(row, 'Status', default='Active')).strip()
                })
        return jsonify({"status": "success", "teachers": teachers})
    except Exception as e:
        return jsonify({"status": "error", "message": _format_friendly_error(e)}), 500


@app.route('/api/admin/assignment_classes')
@role_required('Admin')
def admin_assignment_classes():
    try:
        records = _cached_sheet_records("Students")
        classes = sorted({
            str(_row_value(row, 'Class')).strip()
            for row in records
            if str(_row_value(row, 'Class')).strip()
        })
        return jsonify({"status": "success", "classes": classes})
    except Exception as e:
        return jsonify({"status": "error", "message": _format_friendly_error(e)}), 500


@app.route('/api/admin/teacher_assignments', methods=['GET', 'POST'])
@role_required('Admin')
def admin_teacher_assignments():
    try:
        if request.method == 'GET':
            records = _cached_sheet_records("Teacher_Assignments")
            assignments = [{
                "assignment_id": str(_row_value(row, 'Assignment_ID')).strip(),
                "teacher_id": str(_row_value(row, 'Teacher_Id', 'Teacher_ID', 'Teacher_id')).strip(),
                "teacher_name": str(_row_value(row, 'Teacher_Name', 'Full_Name')).strip(),
                "branch": str(_row_value(row, 'Branch')).strip(),
                "class": str(_row_value(row, 'Class')).strip(),
                "medium": str(_row_value(row, 'Medium')).strip(),
                "subject": str(_row_value(row, 'Subject')).strip(),
                "status": str(_row_value(row, 'Status')).strip()
            } for row in records]
            return jsonify({"status": "success", "assignments": assignments})

        data = request.get_json(silent=True) or {}
        teacher_id = str(data.get('teacher_id', '')).strip()
        teacher_name = str(data.get('teacher_name', '')).strip()
        branch = str(data.get('branch', '')).strip() or 'Main'
        class_name = str(data.get('class_name', '')).strip()
        medium = str(data.get('medium', '')).strip()
        subject = str(data.get('subject', '')).strip()

        if not teacher_id or not class_name:
            return jsonify({"status": "error", "message": "Teacher aur Class select karna zaroori hai."}), 400

        records = _cached_sheet_records("Teacher_Assignments")
        for row in records:
            if (
                _normal_key(_row_value(row, 'Teacher_Id', 'Teacher_ID', 'Teacher_id')) == _normal_key(teacher_id)
                and _normal_key(_row_value(row, 'Class', 'Batch')) == _normal_key(class_name)
                and _normal_key(_row_value(row, 'Subject')) == _normal_key(subject)
                and _normal_key(_row_value(row, 'Branch', default='Main')) == _normal_key(branch)
                and _normal_key(_row_value(row, 'Medium')) == _normal_key(medium)
                and _normal_key(_row_value(row, 'Status', default='Active')) == 'active'
            ):
                return jsonify({"status": "error", "message": "Yeh assignment pehle se maujood hai."}), 409

        numbers = []
        for row in records:
            aid = str(_row_value(row, 'Assignment_ID')).strip()
            if aid.upper().startswith('A'):
                try:
                    numbers.append(int(aid[1:]))
                except ValueError:
                    pass

        assignment_id = f"A{(max(numbers) + 1 if numbers else 1):03d}"
        assignment_sheet = _get_worksheet("Teacher_Assignments")
        _execute_with_retry(assignment_sheet.append_row, [
            assignment_id, teacher_id, teacher_name, branch, class_name, medium, subject, "Active"
        ], value_input_option='USER_ENTERED')
        _invalidate_sheet_cache('Teacher_Assignments')

        return jsonify({
            "status": "success",
            "message": "Teacher assignment successfully save ho gaya!",
            "assignment_id": assignment_id
        })

    except Exception as e:
        print(f"Error in teacher assignments: {e}")
        return jsonify({"status": "error", "message": _format_friendly_error(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
