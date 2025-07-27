import streamlit as st
import json
import os
import random
import string
from datetime import datetime
import graphviz 

# ----------------- File & Directory Setup -----------------
DATA_FILE = "data.json"
TASK_FILE = "tasks.json"
WITHDRAWALS_FILE = "withdrawals.json"
PROOF_STATUS_FILE = "proof_status.json"
os.makedirs("proofs", exist_ok=True) # Ensure 'proofs' directory exists

# ----------------- Utility Functions -----------------
def load_json(file, default):
    """Loads JSON data from a file, creating it with default if it doesn't exist or is empty."""
    if not os.path.exists(file) or os.stat(file).st_size == 0:
        with open(file, "w") as f:
            json.dump(default, f, indent=4)
        return default
    with open(file, "r") as f:
        return json.load(f)

def save_json(file, data):
    """Saves data to a JSON file."""
    with open(file, "w") as f:
        json.dump(data, f, indent=4)

def generate_ref_id(count):
    """Generates a unique referral ID."""
    return f"PRG{1000 + count}"

def generate_password(length=6):
    """Generates a random alphanumeric password."""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))

def find_referrer_ref_id(current_ref_id, data):
    """Finds the ref_id of the user who referred 'current_ref_id'."""
    for u in data["users"].values():
        if u["ref_id"] == current_ref_id:
            return u["ref_by"]
    return None

def find_coadmin_for_member(member_username, data):
    """
    Finds the username of the nearest coadmin in the referral chain for a given member.
    This coadmin will be responsible for approving the member's tasks.
    """
    if member_username not in data["users"]:
        return "UNKNOWN"

    current_user_data = data["users"][member_username]
    current_ref_by_id = current_user_data["ref_by"]

    # Traverse up the referral chain
    while current_ref_by_id != "ROOT": # Assuming "ROOT" is the ultimate parent of admin
        found_referrer_username = None
        for u_key, u_info in data["users"].items():
            if u_info["ref_id"] == current_ref_by_id:
                found_referrer_username = u_key
                break

        if found_referrer_username:
            referrer_info = data["users"][found_referrer_username]
            if referrer_info["role"] == "coadmin":
                return found_referrer_username # Return the coadmin's username
            current_ref_by_id = referrer_info["ref_by"]
        else:
            break # Referrer not found, broken chain or reached end
    return "UNKNOWN" # No coadmin found in the chain


# ----------------- Data Load & Migration (for existing files) -----------------
data = load_json(DATA_FILE, {
    "users": {
        "admin": {
            "name": "Admin",
            "password": "admin123",
            "ref_id": "PRG1001",
            "ref_by": "ROOT",
            "task_income": 0,
            "affiliate_income": 0,
            "joined": str(datetime.now().date()),
            "role": "admin"
        }
    },
    "referrals": {
        "PRG1001": []
    },
    "count": 1
})

tasks = load_json(TASK_FILE, [])
withdrawals = load_json(WITHDRAWALS_FILE, [])
proof_status = load_json(PROOF_STATUS_FILE, {})

# --- Migration for old proof_status entries (to ensure new fields exist) ---
migration_needed = False
for key, value in list(proof_status.items()): # Iterate over a copy if modifying during iteration
    # Ensure 'coadmin_username' exists
    if "coadmin_username" not in value:
        member_username = value.get("member", value.get("member_username"))
        if member_username and member_username in data["users"]:
            coadmin_user = find_coadmin_for_member(member_username, data)
            value["coadmin_username"] = coadmin_user
        else:
            value["coadmin_username"] = "UNKNOWN"
        
        # If status was just "Approved", update to "Approved By Coadmin" for new flow
        if value.get("status") == "Approved":
            value["status"] = "Approved By Coadmin"
        migration_needed = True

    # Ensure 'approved_by_admin' exists
    if "approved_by_admin" not in value:
        value["approved_by_admin"] = False
        migration_needed = True
    
    # Standardize key names (from old to new)
    if "member" in value and "member_username" not in value:
        value["member_username"] = value["member"]
        del value["member"]
        migration_needed = True
    
    if "task" in value and "task_title" not in value:
        value["task_title"] = value["task"]
        del value["task"]
        migration_needed = True
    
    if "amount" in value and "task_payout" not in value:
        value["task_payout"] = value["amount"]
        del value["amount"]
        migration_needed = True

    # Ensure proof_file path is correctly stored and remove old filenames if present
    if "proof_file" not in value:
        # Construct proof_file from old keys if possible
        old_member_name = value.get("member_username", value.get("member", "UNKNOWN_MEMBER"))
        old_task_title = value.get("task_title", value.get("task", "UNKNOWN_TASK")).replace(' ', '_')
        # This part is tricky. If the file extension was hardcoded as .jpg before,
        # and it might be a different type now, this migration won't fix the file name itself,
        # but it will ensure the key exists.
        value["proof_file"] = f"proofs/{old_member_name}_{old_task_title}.jpg" # Default to .jpg for old entries
        migration_needed = True


if migration_needed:
    save_json(PROOF_STATUS_FILE, proof_status)
    st.sidebar.info("Proof status data migrated to new format. Refreshing...")
    st.rerun() # Rerun to apply changes immediately

# ----------------- Auth Functions -----------------
def register_user(name, username, password, referral_id, explicit_role=None): # Added explicit_role
    """Registers a new user and determines role based on referral_id or explicit_role."""
    if username in data["users"]:
        return False, "Username already exists."

    if referral_id not in data["referrals"] and referral_id != "PRG1001":
        return False, "Invalid referral ID."

    # Prevent direct registration under ROOT unless it's the admin
    if referral_id == "ROOT" and username != "admin":
        return False, "Cannot register directly under ROOT."

    # --- Determine the role for the new user ---
    actual_role_for_new_user = "member" # Default to member

    if explicit_role: # If admin explicitly set a role, use that
        actual_role_for_new_user = explicit_role
    elif referral_id == "PRG1001": # Admin's referral ID, and no explicit_role given
        actual_role_for_new_user = "coadmin"
    else:
        # Find the referrer's user info to check their role
        referrer_username = None
        for u_key, u_info in data["users"].items():
            if u_info["ref_id"] == referral_id:
                referrer_username = u_key
                break
        
        # If referrer exists and is a coadmin, the new user is a member under them
        if referrer_username and data["users"][referrer_username]["role"] == "coadmin":
            actual_role_for_new_user = "member" 
        # If the referrer is a member (or not found), the new user also defaults to a member.

    ref_id = generate_ref_id(data["count"])

    data["users"][username] = {
        "name": name,
        "password": password,
        "ref_id": ref_id,
        "ref_by": referral_id,
        "task_income": 0,
        "affiliate_income": 0,
        "joined": str(datetime.now().date()),
        "role": actual_role_for_new_user # Use the determined role
    }
    data["referrals"].setdefault(ref_id, [])
    data["referrals"][referral_id].append(ref_id)

    # Affiliate income calculation
    if actual_role_for_new_user == "member": # Only members generate affiliate income for their upline
        level_payouts = [20, 20, 20, 25, 30, 35, 40, 60]
        current_ref = referral_id
        for level in range(8):
            found_referrer_user_key = None
            for u_key, info in data["users"].items():
                if info["ref_id"] == current_ref:
                    found_referrer_user_key = u_key
                    break
            
            if found_referrer_user_key:
                # Ensure the referrer is not the admin itself for payouts from member registrations
                # Unless admin also receives direct affiliate income from members under them.
                # Assuming affiliate income flows up the chain regardless of referrer role.
                data["users"][found_referrer_user_key]["affiliate_income"] += level_payouts[level]
                current_ref = find_referrer_ref_id(current_ref, data)
                if current_ref is None or current_ref == "ROOT":
                    break
            else:
                break

    data["count"] += 1
    save_json(DATA_FILE, data)
    return True, f"Registered! Your Referral ID is **{ref_id}** | Role: **{actual_role_for_new_user.capitalize()}**"

def login_user(username, password):
    """Authenticates a user."""
    if username in data["users"] and data["users"][username]["password"] == password:
        return True, data["users"][username]
    return False, None

# ----------------- Streamlit UI -----------------
st.set_page_config("PRAGATI PATH - Earn While You Learn", layout="wide")
st.title("🌟 PRAGATI PATH")
st.caption("💼 Earn While You Learn")

# Initialize session state variables for login
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.user = None
    st.session_state.username = None

# Login/Registration Section
if not st.session_state.logged_in:
    st.subheader("🔐 Login")
    username_login = st.text_input("Username", key="username_login")
    password_login = st.text_input("Password", type="password", key="password_login")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Login"):
            success, user = login_user(username_login, password_login)
            if success:
                st.session_state.logged_in = True
                st.session_state.user = user
                st.session_state.username = username_login
                st.rerun()
            else:
                st.error("Invalid credentials.")

    with col2:
        st.markdown("### 🆕 Register")
        with st.form("register_form"):
            name = st.text_input("Full Name", key="reg_name")
            new_user = st.text_input("Create Username", key="reg_username")
            new_pass = st.text_input("Create Password", type="password", key="reg_password")
            ref = st.text_input("Referral ID (Required)", key="reg_ref_id")
            
            # --- Role determination for new user ---
            explicit_role_selection_by_admin = None 
            
            # Check if an admin is logged in AND trying to register directly under their own ref_id
            current_logged_in_user = st.session_state.get("user")
            if st.session_state.logged_in and isinstance(current_logged_in_user, dict):
                logged_in_user_role = current_logged_in_user.get("role")
                logged_in_user_ref_id = current_logged_in_user.get("ref_id")

                if logged_in_user_role == "admin" and ref == logged_in_user_ref_id:
                    explicit_role_selection_by_admin = st.selectbox(
                        "Select Role for New User (Admin Override)", 
                        ["coadmin", "member"], 
                        key="reg_role_select_admin_override"
                    )

            submit = st.form_submit_button("Register Now")
            
            if submit:
                if not name or not new_user or not new_pass or not ref:
                    st.error("Please fill all registration fields.")
                else:
                    success, msg = register_user(name, new_user, new_pass, ref, explicit_role_selection_by_admin)

                    if success:
                        st.success(f"✅ {msg}")
                    else:
                        st.error(msg)
else:
    # Authenticated User Dashboard
    user = st.session_state.user
    username = st.session_state.username
    role = user.get("role", "member")
    st.success(f"Welcome, {user['name']} ({role.capitalize()}) | Your Referral ID: {user['ref_id']}")

    # --- Sidebar Menu ---
    menu_options = ["Dashboard", "Wallet", "Withdrawals"] # Base options for all roles
    if role == "admin":
        menu_options.insert(1, "All Coadmins")
        menu_options.insert(1, "Visual Tree")
        menu_options.insert(1, "Admin: Manage Proofs") # Admin sees proofs approved by coadmins
        menu_options.insert(1, "Task Manager")
        menu_options.insert(1, "Manage Withdrawals") # Admin can manage withdrawals
    elif role == "coadmin":
        menu_options.insert(1, "My Members")
        menu_options.insert(1, "Coadmin: Approve Proofs") # Coadmin sees pending proofs for their members
        menu_options.insert(1, "Task Manager")
    elif role == "member":
        menu_options.insert(1, "Tasks") # Members see available tasks to submit proof for

    menu = st.sidebar.selectbox("📂 Menu", menu_options)

    # --- Menu Page Logic ---
    if menu == "Dashboard":
        st.subheader("📊 Dashboard")
        ref_id = user["ref_id"]
        directs = data["referrals"].get(ref_id, [])

        st.write(f"👥 Direct Referrals: **{len(directs)}**")

        def count_team(ref, current_data, level=1):
            """Recursively counts the total team members up to 8 levels."""
            if level > 8:
                return 0
            count = len(current_data["referrals"].get(ref, []))
            for child in current_data["referrals"].get(ref, []):
                count += count_team(child, current_data, level + 1)
            return count

        st.write(f"🌐 Total Team (up to 8 levels): **{count_team(ref_id, data)}**")
        st.write(f"📅 Joined on: **{user['joined']}**")

    # --- Task Manager ---
    elif menu == "Task Manager" and role in ["admin", "coadmin"]:
        st.subheader("🛠 Task Manager")
        with st.form("new_task"):
            title = st.text_input("Task Title")
            desc = st.text_area("Task Description")
            payout = st.number_input("Payout (₹)", min_value=10, max_value=5000, value=100)
            submit_task = st.form_submit_button("Add Task")
            if submit_task:
                if not title or not desc or not payout:
                    st.error("Please fill all task details.")
                else:
                    tasks.append({
                        "title": title,
                        "description": desc,
                        "payout": payout,
                        "created_by": username,
                        "created_date": str(datetime.now().date())
                    })
                    save_json(TASK_FILE, tasks)
                    st.success("✅ Task Added Successfully!")
    
    # --- Tasks (for Members) ---
    elif menu == "Tasks": # This block is exclusively for members to see and submit tasks
        st.subheader("📋 Available Tasks")
        if not tasks:
            st.info("No tasks available currently. Please check back later!")
        
        for i, t in enumerate(tasks):
            # Check if this member has already submitted for this task and it's pending/approved/denied
            proof_exists_for_task = False
            current_proof_status_for_task = "N/A"
            for pk, pv in proof_status.items():
                if pv["member_username"] == username and pv["task_title"] == t["title"]:
                    proof_exists_for_task = True
                    current_proof_status_for_task = pv["status"]
                    break

            with st.expander(f"{t['title']} (₹{t['payout']})"):
                st.write(t["description"])
                st.caption(f"Created by: {t.get('created_by', 'Admin')} on {t.get('created_date', 'N/A')}")
                
                if proof_exists_for_task:
                    st.info(f"You have already submitted proof for this task. Status: **{current_proof_status_for_task}**")
                else:
                    proof = st.file_uploader(
                        "Upload Proof (Images, PDFs, PPTs, Word Docs, Videos)",
                        type=["jpg", "jpeg", "png", "pdf", "pptx", "doc", "docx", "mp4", "avi", "mov", "webm"],
                        key=f"upload_proof_{t['title']}_{i}" # Unique key for each uploader
                    )
                    if proof:
                        unique_key_for_proof = f"{username}_{t['title'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
                        
                        file_extension = os.path.splitext(proof.name)[1].lower() # Get original extension and convert to lowercase
                        fname = f"proofs/{unique_key_for_proof}{file_extension}" # Save with original extension
                        
                        try:
                            with open(fname, "wb") as f:
                                f.write(proof.read())
                            
                            responsible_coadmin_username = find_coadmin_for_member(username, data)

                            proof_status[unique_key_for_proof] = {
                                "task_title": t["title"],
                                "task_payout": t["payout"],
                                "member_username": username,
                                "proof_file": fname, # Path to the saved file
                                "status": "Pending",
                                "submitted_date": str(datetime.now().date()),
                                "coadmin_username": responsible_coadmin_username,
                                "approved_by_admin": False
                            }
                            save_json(PROOF_STATUS_FILE, proof_status)
                            st.success("✅ Proof Submitted Successfully for Approval!")
                            st.rerun() # Rerun to update the UI
                        except Exception as e:
                            st.error(f"Error uploading proof: {e}")

    # --- Wallet ---
    elif menu == "Wallet":
        st.subheader("💼 Your Wallet")
        current_user_data = data["users"].get(username, {})
        st.write(f"💸 Affiliate Income: **₹{current_user_data.get('affiliate_income', 0)}**")
        st.write(f"📝 Task Earnings: **₹{current_user_data.get('task_income', 0)}**")
        total_balance = current_user_data.get('affiliate_income', 0) + current_user_data.get('task_income', 0)
        st.write(f"💰 Total Balance: **₹{total_balance}**")

    # --- Withdrawals ---
    elif menu == "Withdrawals":
        st.subheader("🏧 Request Withdrawal")
        current_user_data = data["users"].get(username, {})
        total_withdrawable_amount = current_user_data.get('affiliate_income', 0) + current_user_data.get('task_income', 0)
        
        st.write(f"Your current withdrawable balance: **₹{total_withdrawable_amount}**")
        
        if total_withdrawable_amount <= 0:
            st.info("You have no balance to withdraw.")
        else:
            with st.form("request_withdrawal_form"):
                upi = st.text_input("Enter your UPI ID")
                request_withdraw_button = st.form_submit_button("Request Withdrawal")
                if request_withdraw_button:
                    if not upi:
                        st.error("Please enter your UPI ID.")
                    else:
                        withdrawals.append({
                            "request_id": f"WDR{len(withdrawals) + 1}-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                            "username": username,
                            "name": user["name"],
                            "upi": upi,
                            "amount": total_withdrawable_amount,
                            "date": str(datetime.now().date()),
                            "status": "Pending" # Add status for withdrawals
                        })
                        # Reset user balances after withdrawal request
                        data["users"][username]["affiliate_income"] = 0
                        data["users"][username]["task_income"] = 0
                        
                        save_json(DATA_FILE, data)
                        save_json(WITHDRAWALS_FILE, withdrawals)
                        st.success("✅ Withdrawal request submitted. Payment will be processed soon.")
                        st.rerun()

    # --- Manage Withdrawals (for Admin) ---
    elif menu == "Manage Withdrawals" and role == "admin":
        st.subheader("💰 Manage Withdrawal Requests")
        
        pending_withdrawals = [w for w in withdrawals if w["status"] == "Pending"]

        if not pending_withdrawals:
            st.info("No pending withdrawal requests.")
        
        for i, req in enumerate(pending_withdrawals):
            with st.expander(f"💰 Request from {req['username']} - ₹{req['amount']} ({req['date']})"):
                st.write(f"**Requester:** {req['name']} ({req['username']})")
                st.write(f"**UPI ID:** `{req['upi']}`")
                st.write(f"**Amount:** ₹{req['amount']}")
                st.write(f"**Request ID:** {req['request_id']}")
                
                col_mark_paid, col_cancel = st.columns(2)
                with col_mark_paid:
                    if st.button("✅ Mark as Paid", key=f"pay_withdrawal_{i}"):
                        for w in withdrawals:
                            if w["request_id"] == req["request_id"]:
                                w["status"] = "Paid"
                                break
                        save_json(WITHDRAWALS_FILE, withdrawals)
                        st.success(f"Withdrawal request {req['request_id']} marked as Paid.")
                        st.rerun()
                with col_cancel:
                    if st.button("❌ Cancel Request", key=f"cancel_withdrawal_{i}"):
                        for w in withdrawals:
                            if w["request_id"] == req["request_id"]:
                                w["status"] = "Cancelled"
                                # Optionally, refund the amount to the user's wallet
                                # data["users"][req["username"]]["task_income"] += req["amount"] # Or affiliate income, depending on how you structure it
                                # save_json(DATA_FILE, data)
                                break
                        save_json(WITHDRAWALS_FILE, withdrawals)
                        st.warning(f"Withdrawal request {req['request_id']} cancelled.")
                        st.rerun()
        
        st.markdown("---")
        st.subheader("Processed Withdrawals")
        processed_withdrawals = [w for w in withdrawals if w["status"] != "Pending"]
        if not processed_withdrawals:
            st.info("No processed withdrawal requests.")
        else:
            for req in processed_withdrawals:
                st.write(f"- {req['username']} | ₹{req['amount']} | Status: **{req['status']}** | Date: {req['date']}")


    # --- My Members (for Coadmin) ---
    elif menu == "My Members" and role == "coadmin":
        st.subheader("👥 Your Direct Members")
        own_ref_id = user["ref_id"]
        # Find members who were directly referred by this coadmin
        my_direct_members = [
            (uname, info) for uname, info in data["users"].items() 
            if info["ref_by"] == own_ref_id and info["role"] == "member"
        ]

        if not my_direct_members:
            st.info("You currently have no direct members.")
        
        for member_username, member_info in my_direct_members:
            with st.expander(f"{member_info['name']} ({member_username} | {member_info['ref_id']})"):
                st.write(f"📅 Joined: {member_info['joined']}")
                st.write(f"💸 Affiliate Income: ₹{member_info['affiliate_income']}")
                st.write(f"📝 Task Income: ₹{member_info['task_income']}")
                
                # Using a form for delete to prevent accidental deletions on rerun
                with st.form(key=f"delete_member_form_{member_info['ref_id']}"):
                    st.warning(f"Do you want to delete {member_info['name']}?")
                    delete_confirmed = st.form_submit_button(f"Confirm Delete {member_info['name']}")
                    if delete_confirmed:
                        # Clean up referrals
                        if member_info['ref_id'] in data["referrals"]:
                            del data["referrals"][member_info['ref_id']]
                        if own_ref_id in data["referrals"] and member_info['ref_id'] in data["referrals"][own_ref_id]:
                            data["referrals"][own_ref_id].remove(member_info["ref_id"])
                        
                        # Remove from users
                        if member_username in data["users"]:
                            del data["users"][member_username]
                        
                        save_json(DATA_FILE, data)
                        st.success(f"Deleted member {member_info['name']}")
                        st.rerun()

        st.markdown("---")
        st.subheader("➕ Add New Member (Under You)")
        with st.form("add_member_form"):
            mem_name = st.text_input("Full Name for New Member", key="add_mem_name")
            mem_username = st.text_input("Username for New Member", key="add_mem_username")
            mem_password = generate_password() # Auto-generate password
            
            st.info(f"Generated Password: `{mem_password}` (Please copy this for the new member)")
            submitted = st.form_submit_button("Add Member")
            if submitted:
                if not mem_name or not mem_username:
                    st.error("Please fill full name and username.")
                else:
                    # Coadmins can only add members under them, so no explicit_role needed here
                    success, msg = register_user(mem_name, mem_username, mem_password, own_ref_id) 
                    if success:
                        st.success(f"✅ Member Added: {mem_username}\nPassword: {mem_password}")
                        st.code(f"Username: {mem_username}\nPassword: {mem_password}", language="text")
                        st.rerun()
                    else:
                        st.error(msg)
    
    # --- Coadmin: Approve Proofs ---
    elif menu == "Coadmin: Approve Proofs" and role == "coadmin":
        st.subheader("✅ Approve Task Proofs (Your Members)")
        coadmin_username = st.session_state.username

        # Filter for pending proofs that are assigned to this coadmin
        pending_proofs_for_coadmin = {
            k: v for k, v in proof_status.items() 
            if v["status"] == "Pending" and v["coadmin_username"] == coadmin_username
        }

        if not pending_proofs_for_coadmin:
            st.info("No pending proofs from your members.")
        
        for key, value in pending_proofs_for_coadmin.items():
            with st.expander(f"📝 {value['member_username']} - {value['task_title']} (Submitted on {value['submitted_date']})"):
                st.write(f"Task Payout: ₹{value['task_payout']}")
                
                proof_file_path = value['proof_file']
                file_ext = os.path.splitext(proof_file_path)[1].lower() # Get extension and convert to lowercase

                try:
                    if file_ext in ['.jpg', '.jpeg', '.png']:
                        st.image(proof_file_path, caption="Proof Image", width=300)
                    elif file_ext == '.pdf':
                        st.write("PDF file uploaded. Use the download button to view.")
                        with open(proof_file_path, "rb") as file:
                            st.download_button(
                                label="Download PDF",
                                data=file,
                                file_name=os.path.basename(proof_file_path),
                                mime="application/pdf",
                                key=f"download_coadmin_pdf_{key}"
                            )
                    elif file_ext in ['.doc', '.docx']:
                        st.write("Word Document uploaded. Use the download button to view.")
                        with open(proof_file_path, "rb") as file:
                            st.download_button(
                                label="Download Word Doc",
                                data=file,
                                file_name=os.path.basename(proof_file_path),
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                key=f"download_coadmin_doc_{key}"
                            )
                    elif file_ext in ['.mp4', '.avi', '.mov', '.webm']:
                        st.video(proof_file_path, format=f"video/{file_ext.strip('.')}")
                        st.write("Video file uploaded. Use the download button if playback fails.")
                        with open(proof_file_path, "rb") as file:
                            st.download_button(
                                label="Download Video",
                                data=file,
                                file_name=os.path.basename(proof_file_path),
                                mime=f"video/{file_ext.strip('.')}",
                                key=f"download_coadmin_video_{key}"
                            )
                    elif file_ext == '.pptx':
                         st.write("PowerPoint Presentation uploaded. Use the download button to view.")
                         with open(proof_file_path, "rb") as file:
                            st.download_button(
                                label="Download PPTX",
                                data=file,
                                file_name=os.path.basename(proof_file_path),
                                mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                                key=f"download_coadmin_pptx_{key}"
                            )
                    else: # Fallback for any other file type
                        st.write(f"Proof file type not supported for direct preview: {file_ext}. Please download.")
                        with open(proof_file_path, "rb") as file:
                            st.download_button(
                                label=f"Download {file_ext.upper()} File",
                                data=file,
                                file_name=os.path.basename(proof_file_path),
                                key=f"download_coadmin_other_{key}"
                            )

                except FileNotFoundError:
                    st.error("Proof file not found. It might have been deleted.")

                col_approve, col_deny = st.columns(2)
                with col_approve:
                    if st.button(f"✅ Approve Task", key=f"approve_{key}"):
                        member_user = value["member_username"]
                        task_amount = value["task_payout"]
                        
                        if member_user in data["users"]:
                            data["users"][member_user]["task_income"] += task_amount
                            proof_status[key]["status"] = "Approved By Coadmin" # New status after coadmin approval
                            save_json(DATA_FILE, data)
                            save_json(PROOF_STATUS_FILE, proof_status)
                            st.success(f"Approved {member_user}'s task! Income added to wallet.")
                            st.rerun()
                        else:
                            st.error("Member not found in database.")

                with col_deny:
                    if st.button(f"❌ Deny Task", key=f"deny_{key}"):
                        proof_status[key]["status"] = "Denied By Coadmin" # New status for denied tasks
                        save_json(PROOF_STATUS_FILE, proof_status)
                        st.warning(f"Denied {value['member_username']}'s task.")
                        st.rerun()

    # --- Admin: Manage Proofs ---
    elif menu == "Admin: Manage Proofs" and role == "admin":
        st.subheader("🕵️‍♀️ All Approved Task Proofs (For Admin Review)")

        # Filter for proofs that have been approved by a coadmin
        approved_by_coadmin_proofs = {
            k: v for k, v in proof_status.items()
            if v["status"] == "Approved By Coadmin"
        }
        
        if not approved_by_coadmin_proofs:
            st.info("No proofs have been approved by coadmins yet.")
        
        for key, value in approved_by_coadmin_proofs.items():
            with st.expander(f"✅ {value['member_username']} - {value['task_title']} (Approved by {value['coadmin_username']})"):
                st.write(f"Task Payout: ₹{value['task_payout']}")
                st.write(f"Submitted On: {value['submitted_date']}")
                st.write(f"Approved by Coadmin: **{value['coadmin_username']}**")
                
                proof_file_path = value['proof_file']
                file_ext = os.path.splitext(proof_file_path)[1].lower() # Get extension and convert to lowercase

                try:
                    if file_ext in ['.jpg', '.jpeg', '.png']:
                        st.image(proof_file_path, caption="Proof Image", width=300)
                    elif file_ext == '.pdf':
                        st.write("PDF file uploaded. Use the download button to view.")
                        with open(proof_file_path, "rb") as file:
                            st.download_button(
                                label="Download PDF",
                                data=file,
                                file_name=os.path.basename(proof_file_path),
                                mime="application/pdf",
                                key=f"download_admin_pdf_{key}"
                            )
                    elif file_ext in ['.doc', '.docx']:
                        st.write("Word Document uploaded. Use the download button to view.")
                        with open(proof_file_path, "rb") as file:
                            st.download_button(
                                label="Download Word Doc",
                                data=file,
                                file_name=os.path.basename(proof_file_path),
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                key=f"download_admin_doc_{key}"
                            )
                    elif file_ext in ['.mp4', '.avi', '.mov', '.webm']:
                        st.video(proof_file_path, format=f"video/{file_ext.strip('.')}")
                        st.write("Video file uploaded. Use the download button if playback fails.")
                        with open(proof_file_path, "rb") as file:
                            st.download_button(
                                label="Download Video",
                                data=file,
                                file_name=os.path.basename(proof_file_path),
                                mime=f"video/{file_ext.strip('.')}",
                                key=f"download_admin_video_{key}"
                            )
                    elif file_ext == '.pptx':
                        st.write("PowerPoint Presentation uploaded. Use the download button to view.")
                        with open(proof_file_path, "rb") as file:
                            st.download_button(
                                label="Download PPTX",
                                data=file,
                                file_name=os.path.basename(proof_file_path),
                                mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                                key=f"download_admin_pptx_{key}"
                            )
                    else: # Fallback for any other file type
                        st.write(f"Proof file type not supported for direct preview: {file_ext}. Please download.")
                        with open(proof_file_path, "rb") as file:
                            st.download_button(
                                label=f"Download {file_ext.upper()} File",
                                data=file,
                                file_name=os.path.basename(proof_file_path),
                                key=f"download_admin_other_{key}"
                            )

                except FileNotFoundError:
                    st.error("Proof file not found. It might have been deleted.")

                # Admin can mark as reviewed/processed
                if not value.get("approved_by_admin", False):
                    if st.button(f"Confirm Admin Review", key=f"admin_confirm_{key}"):
                        proof_status[key]["approved_by_admin"] = True
                        proof_status[key]["status"] = "Approved By Admin" # Final status after admin approval
                        save_json(PROOF_STATUS_FILE, proof_status)
                        st.success("Admin review confirmed for this proof. Status updated.")
                        st.rerun()
                else:
                    st.info("This proof has been reviewed by Admin.")
    
    # --- All Coadmins (for Admin) ---
    elif menu == "All Coadmins" and role == "admin":
        st.subheader("🧑‍💼 All Coadmins")
        coadmins = {u: info for u, info in data["users"].items() if info["role"] == "coadmin"}

        if not coadmins:
            st.info("No coadmins registered yet.")
            
        for uname, info in coadmins.items():
            with st.expander(f"{info['name']} ({uname})"):
                st.write(f"📅 Joined: {info['joined']}")
                st.write(f"👥 Referral ID: **{info['ref_id']}**")

                st.markdown("##### View Options")
                if st.button(f"👥 View Direct Members under {uname}", key=f"members_under_{uname}"):
                    members = [
                        m for m in data["users"].values()
                        if m["ref_by"] == info["ref_id"] and m["role"] == "member"
                    ]
                    if members:
                        st.markdown("###### Direct Members:")
                        for mem in members:
                            st.markdown(f"- **{mem['name']}** ({mem['ref_id']}) - Task Income: ₹{mem['task_income']}, Affiliate Income: ₹{mem['affiliate_income']}")
                    else:
                        st.info(f"No direct members found under {info['name']}.")

                if st.button(f"📝 View Tasks Created by {uname}", key=f"tasks_created_by_{uname}"):
                    user_tasks = [t for t in tasks if t.get("created_by") == uname]
                    if user_tasks:
                        st.markdown("###### Created Tasks:")
                        for t in user_tasks:
                            st.markdown(f"- **{t['title']}** | Payout: ₹{t['payout']} | Created on: {t.get('created_date', 'N/A')}")
                    else:
                        st.info(f"No tasks created by {info['name']}.")

    # --- Visual Tree (for Admin) ---
    elif menu == "Visual Tree" and role == "admin":
        st.subheader("🌳 Visual Referral Tree")

        dot = graphviz.Digraph(comment='Referral Tree')
        dot.attr(rankdir='LR') # Left to Right orientation for better readability

        visited_nodes = set() # Use a different name for clarity

        def add_nodes_edges_to_graph(ref_id):
            """Recursively adds nodes and edges to the graphviz diagram."""
            if ref_id in visited_nodes:
                return
            visited_nodes.add(ref_id)
            
            # Find user info for the current ref_id
            current_user_name = "Unknown"
            current_user_role = "Unknown"
            current_user_username = "Unknown" # Get username for display
            for u_key, u_info in data["users"].items():
                if u_info["ref_id"] == ref_id:
                    current_user_name = u_info["name"]
                    current_user_role = u_info["role"]
                    current_user_username = u_key
                    break

            # Set node style based on role
            node_color = "lightblue"
            if current_user_role == "admin":
                node_color = "red"
            elif current_user_role == "coadmin":
                node_color = "lightgreen"
            
            dot.node(ref_id, f"{current_user_name}\n({current_user_username})\n[{current_user_role.capitalize()}]", 
                     style='filled', fillcolor=node_color, shape='box')
            
            children = data["referrals"].get(ref_id, [])
            for child_ref_id in children:
                # Add edge
                dot.edge(ref_id, child_ref_id)
                # Recursively add children
                add_nodes_edges_to_graph(child_ref_id)

        root_ref = data["users"]["admin"]["ref_id"]
        add_nodes_edges_to_graph(root_ref)
        
        st.graphviz_chart(dot)

# ---------------- Sidebar & Logout ----------------
with st.sidebar:
    st.markdown("---")
    if st.session_state.get("logged_in", False):
        if st.button("🚪 Logout"):
            st.session_state.logged_in = False
            st.session_state.user = None
            st.session_state.username = None
            st.rerun()
