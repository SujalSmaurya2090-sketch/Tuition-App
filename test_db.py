# ==========================================
# FILE: test_db.py
# PURPOSE: Google Sheets API Connection Test
# ==========================================

import gspread
from google.oauth2.service_account import Credentials

# 1. Google API ki permissions/scopes defining
# Ye scopes bataate hain ki hume Drive aur Sheets ka full access chahiye.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def connect_to_sheet():
    try:
        # 2. Secret JSON key se login authenticate karna
        # credentials.json file same folder mein honi chahiye.
        creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
        client = gspread.authorize(creds)
        
        # 3. Target Google Sheet ko open karna
        # Yahan sheet ka exact name wahi hona chahiye jo aapne Google Sheets mein rakha hai.
        sheet = client.open("Tuition_Master_Database")
        
        print(" Success! Google Sheet Connected Successfully.")
        print("Available Tabs in Sheet:", [ws.title for ws in sheet.worksheets()])
        
        # 4. Pehle tab ('Students_Master') mein test data write karna
        worksheet = sheet.worksheet("Students_Master")
        
        # Headers check karke set karna (agar tab khali hai toh)
        headers = ["Student_ID", "Full_Name", "Class", "Parent_Contact", "Monthly_Fee", "Status", "Joining_Date"]
        existing_data = worksheet.get_all_values()
        
        if len(existing_data) == 0:
            worksheet.append_row(headers)
            print(" Headers added to 'Students_Master' tab!")
        else:
            print(" Headers already exist in 'Students_Master'.")
            
    except FileNotFoundError:
        print(" Error: 'credentials.json' file nahi mili! Folder check karein.")
    except gspread.exceptions.SpreadsheetNotFound:
        print(" Error: 'Tuition_Master_Database' naam ki sheet nahi mili ya share nahi ki gayi.")
    except Exception as e:
        print(f" Error occurred: {str(e)}")

# Script execution start point
if __name__ == "__main__":
    connect_to_sheet()