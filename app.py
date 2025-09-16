from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import os
from datetime import date, datetime, timezone
from supabase import create_client, Client
import humanize
from dateutil.relativedelta import relativedelta
import random

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", os.urandom(24))



@app.template_filter('humanize_datetime')
def humanize_datetime_filter(dt_string):
    dt = datetime.fromisoformat(dt_string).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return humanize.naturaltime(now - dt)

@app.after_request
def add_header(response):
    """
    Add headers to force the browser not to cache authenticated pages
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# Supabase connection
SUPABASE_URL = "https://uccwrkpdkheliitelkag.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVjY3dya3Bka2hlbGlpdGVsa2FnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTcwNDM5OTUsImV4cCI6MjA3MjYxOTk5NX0.fHF4N9m2n5FIGQRbhjxdq9YBolFoVkVJJ5VIzRFL3h8"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def calculate_age(born):
    today = date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))

def get_unread_chat_count(user_id):
    """Counts the number of distinct users who have sent an unread message."""
    res = supabase.table("messages").select("sender_id").eq("receiver_id", user_id).eq("is_read", False).execute()
    unique_senders = set(item['sender_id'] for item in res.data)
    return len(unique_senders)

def get_unread_count_per_chat(user_id):
    """
    Counts unread messages for a user, grouped by sender.
    Returns a dictionary like { 'sender_id': count, ... }
    """
    res = supabase.table("messages").select("sender_id").eq("receiver_id", user_id).eq("is_read", False).execute()
    unread_counts = {}
    for message in res.data:
        sender_id = message['sender_id']
        unread_counts[sender_id] = unread_counts.get(sender_id, 0) + 1
    return unread_counts

def mark_messages_as_read(receiver_id, sender_id):
    res = supabase.table("messages") \
    .update({"is_read": True}) \
    .eq("sender_id", sender_id) \
    .eq("receiver_id", receiver_id) \
    .execute()

    print(res)


def add_like(liker_id, liked_id):
    """Insert a like if it doesn't exist already."""
    res = supabase.table("likes").insert({
        "liker_id": liker_id,
        "liked_id": liked_id
    }).execute()
    return res

def remove_like(liker_id, liked_id):
    """Remove an existing like."""
    res = supabase.table("likes").delete() \
        .eq("liker_id", liker_id).eq("liked_id", liked_id).execute()
    return res

def has_liked(liker_id, liked_id):
    """Check if the user already liked the profile."""
    res = supabase.table("likes").select("id") \
        .eq("liker_id", liker_id).eq("liked_id", liked_id).execute()
    return bool(res.data)

def get_likes_count(user_id):
    """Return how many likes this profile has received."""
    res = supabase.table("likes").select("id", count="exact") \
        .eq("liked_id", user_id).execute()
    return res.count or 0

def get_match_count(user_id):
    """Return how many times this profile has been selected in Start Matching."""
    res = supabase.table("match_history").select("id", count="exact") \
        .eq("matched_id", user_id).execute()
    return res.count or 0


def get_chat_partners_count(user_id):
    """Returns the number of unique users a profile has chatted with."""
    res = supabase.table("messages").select("sender_id, receiver_id") \
        .or_(f"sender_id.eq.{user_id},receiver_id.eq.{user_id}").execute()
    
    chat_partners = set()
    for message in res.data:
        if message['sender_id'] != user_id:
            chat_partners.add(message['sender_id'])
        if message['receiver_id'] != user_id:
            chat_partners.add(message['receiver_id'])
    return len(chat_partners)
# END: New function

from flask import url_for

def get_recent_activity(user_id):
    activities = []

    # 1. Messages
    messages_res = supabase.table("messages") \
        .select("sender_id, content, created_at") \
        .eq("receiver_id", user_id) \
        .order("created_at", desc=True) \
        .limit(5).execute()
    if messages_res.data:
        sender_ids = [m['sender_id'] for m in messages_res.data]
        profiles_res = supabase.table("profiles").select("id, name").in_("id", sender_ids).execute()
        profiles_map = {p['id']: p['name'] for p in profiles_res.data}
        for msg in messages_res.data:
            sender_name = profiles_map.get(msg['sender_id'], 'Someone')
            activities.append({
                "type": "message",
                "message": f"New message from {sender_name}",
                "timestamp": msg['created_at'],
                "link": url_for("view_profile", user_id=msg['sender_id'])
            })

    # 2. Likes
    likes_res = supabase.table("likes") \
        .select("liker_id, created_at") \
        .eq("liked_id", user_id) \
        .order("created_at", desc=True) \
        .limit(5).execute()
    if likes_res.data:
        liker_ids = [l['liker_id'] for l in likes_res.data]
        profiles_res = supabase.table("profiles").select("id, name").in_("id", liker_ids).execute()
        profiles_map = {p['id']: p['name'] for p in profiles_res.data}
        for like in likes_res.data:
            liker_name = profiles_map.get(like['liker_id'], 'Someone')
            activities.append({
                "type": "like",
                "message": f"{liker_name} liked your profile",
                "timestamp": like['created_at'],
                "link": url_for("view_profile", user_id=like['liker_id'])
            })

    # 3. Matches (from match_activity to allow repeats)
    match_res = supabase.table("match_activity") \
        .select("user_id, created_at") \
        .eq("matched_id", user_id) \
        .order("created_at", desc=True) \
        .limit(5).execute()
    if match_res.data:
        matcher_ids = [m['user_id'] for m in match_res.data]
        profiles_res = supabase.table("profiles").select("id, name").in_("id", matcher_ids).execute()
        profiles_map = {p['id']: p['name'] for p in profiles_res.data}
        for match in match_res.data:
            matcher_name = profiles_map.get(match['user_id'], 'Someone')
            activities.append({
                "type": "match",
                "message": f"{matcher_name} matched with you",
                "timestamp": match['created_at'],
                "link": url_for("view_profile", user_id=match['user_id'])
            })

    return sorted(activities, key=lambda x: x['timestamp'], reverse=True)




@app.route('/')
def landing():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('landing.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        res = supabase.table("users").select("*").eq("email", email).execute()
        user = res.data[0] if res.data else None
        if user and user["password"] == password:
            session['user_id'] = user["id"]
            if user.get("is_profile_complete"):
                return redirect(url_for('dashboard'))
            else:
                return redirect(url_for('profile_setup'))
        else:
            flash('Invalid credentials. Please try again.', 'error')
            return redirect(url_for('login'))
    return render_template('auth.html', mode='login')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        res = supabase.table("users").select("id").eq("email", email).execute()
        if res.data:
            flash('An account with this email already exists.', 'error')
            return redirect(url_for('signup'))
        res = supabase.table("users").insert({
            "email": email,
            "password": password,
            "is_profile_complete": False
        }).execute()
        user_id = res.data[0]["id"]
        session['user_id'] = user_id
        return redirect(url_for('profile_setup'))
    return render_template('auth.html', mode='signup')

@app.route('/profile-setup', methods=['GET', 'POST'])
def profile_setup():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        user_id = session['user_id']

        # --- Step 2: DOB + Age ---
        dob_str = request.form.get("dateOfBirth")
        age = 0
        if dob_str:
            try:
                born = date.fromisoformat(dob_str)
                age = calculate_age(born)
            except ValueError:
                pass

        # --- Step 4: Photo Uploads ---
        uploaded_photos = request.files.getlist("photos")
        photo_urls = []
        if uploaded_photos:
            for photo in uploaded_photos:
                if photo and photo.filename:
                    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
                    file_name = f"{user_id}_{timestamp}_{photo.filename}"
                    temp_path = os.path.join("uploads", file_name)
                    os.makedirs("uploads", exist_ok=True)
                    photo.save(temp_path)

                    # Upload to Supabase Storage
                    with open(temp_path, "rb") as f:
                        supabase.storage.from_("profile_photos").upload(
                            file_name, f, {"upsert": "true"}
                        )

                    # Get Public URL
                    public_url = supabase.storage.from_("profile_photos").get_public_url(file_name)
                    photo_urls.append(public_url)

                    # Remove temp file
                    os.remove(temp_path)

        # Default photo if none uploaded
        if not photo_urls:
            photo_urls = [
                "https://images.pexels.com/photos/220453/pexels-photo-220453.jpeg?auto=compress&cs=tinysrgb&w=400&h=400&fit=crop&crop=face"
            ]

        # --- Step 5: Personal Preferences ---
        aesthetics = request.form.get("aesthetics_custom") or request.form.get("aesthetics")
        relationship = request.form.get("relationship_custom") or request.form.get("relationship")
        fun_option = request.form.get("fun_option_custom") or request.form.get("fun_option")
        hangout = request.form.get("hangout_custom") or request.form.get("hangout")
        looking_for = request.form.get("looking_for")

        # --- Save Profile ---
        supabase.table("profiles").upsert({
            "id": user_id,
            "name": request.form.get("name"),
            "dob": dob_str,
            "age": age,
            "gender": request.form.get("gender"),
            "location": request.form.get("location"),
            "bio": request.form.get("bio"),
            "interests": [i.strip() for i in request.form.get("interests", "").split(',') if i.strip()],
            "photos": photo_urls,
            "aesthetics": aesthetics,
            "relationship": relationship,
            "fun_option": fun_option,
            "hangout": hangout,
            "looking_for": looking_for
        }).execute()

        # Mark user as profile complete
        supabase.table("users").update({"is_profile_complete": True}).eq("id", user_id).execute()

        return redirect(url_for('dashboard'))

    return render_template('profile_setup.html')


@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    res_user = supabase.table("users").select("*").eq("id", user_id).execute()
    res_profile = supabase.table("profiles").select("*").eq("id", user_id).execute()
    user = res_user.data[0] if res_user.data else None
    profile = res_profile.data[0] if res_profile.data else None
    
    if profile and profile['photos'] is None:
        profile['photos'] = []
    
    if not user or not user.get("is_profile_complete"):
        return redirect(url_for('profile_setup'))

    unread_chat_count = get_unread_chat_count(user_id)
    likes_count = get_likes_count(user_id)
    match_count = get_match_count(user_id)
    messages_count = get_chat_partners_count(user_id)
    
    # Fetch dynamic recent activity
    recent_activity = get_recent_activity(user_id)

    session['back_url'] = url_for('dashboard')
    
    return render_template(
        'dashboard.html',
        user=user,
        profile=profile,
        unread_chat_count=unread_chat_count,
        likes_count=likes_count,
        match_count=match_count,
        messages_count=messages_count,
        recent_activity=recent_activity
    )

@app.route('/edit-profile', methods=['GET'])
def edit_profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    
    # Fetch current profile data
    res_profile = supabase.table("profiles").select("*").eq("id", user_id).execute()
    profile = res_profile.data[0] if res_profile.data else None
    
    if not profile:
        flash("Profile not found.", "error")
        return redirect(url_for('dashboard'))
        
    return render_template('edit_profile.html', profile=profile)


# Route for handling the form submission
@app.route('/update-profile', methods=['POST'])
def update_profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user_id = session['user_id']

    # Fetch existing photos
    res = supabase.table("profiles").select("photos").eq("id", user_id).execute()
    existing_photos = res.data[0]['photos'] if res.data and res.data[0]['photos'] else []

    # Handle profile picture update (replace first photo)
    profile_picture = request.files.get("profile_picture")
    if profile_picture and profile_picture.filename:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        file_name = f"{user_id}_profile_{timestamp}_{profile_picture.filename}"
        temp_path = os.path.join("uploads", file_name)
        os.makedirs("uploads", exist_ok=True)
        profile_picture.save(temp_path)

        with open(temp_path, "rb") as f:
            supabase.storage.from_("profile_photos").upload(
                file_name, f, {"upsert": "true"}
            )

        profile_url = supabase.storage.from_("profile_photos").get_public_url(file_name)
        os.remove(temp_path)

        # Replace the first photo (main profile pic)
        if existing_photos:
            existing_photos[0] = profile_url
        else:
            existing_photos = [profile_url]

    # Handle additional photo uploads
    uploaded_photos = request.files.getlist("photos")
    new_photos = []
    for photo in uploaded_photos:
        if photo and photo.filename:
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
            file_name = f"{user_id}_{timestamp}_{photo.filename}"
            temp_path = os.path.join("uploads", file_name)
            os.makedirs("uploads", exist_ok=True)
            photo.save(temp_path)
            with open(temp_path, "rb") as f:
                supabase.storage.from_("profile_photos").upload(
                    file_name, f, {"upsert": "true"}
                )
            public_url = supabase.storage.from_("profile_photos").get_public_url(file_name)
            new_photos.append(public_url)
            os.remove(temp_path)

    updated_photos = existing_photos + new_photos

    # Age calculation
    dob_str = request.form.get("dateOfBirth")
    age = None
    if dob_str:
        try:
            born = date.fromisoformat(dob_str)
            age = calculate_age(born)
        except ValueError:
            pass

    # New fields
    gender = request.form.get("gender")
    interestedIn = request.form.getlist("interestedIn")  # multiple checkboxes
    aesthetics = request.form.get("aesthetics")
    aesthetics_custom = request.form.get("aesthetics_custom")
    relationship = request.form.get("relationship")
    relationship_custom = request.form.get("relationship_custom")

    # Handle "Other" overrides
    if aesthetics == "Other" and aesthetics_custom:
        aesthetics = aesthetics_custom

    if relationship == "Other" and relationship_custom:
        relationship = relationship_custom

    # Prepare update data
    update_data = {
        "name": request.form.get("name"),
        "dob": dob_str,
        "age": age,
        "location": request.form.get("location"),
        "bio": request.form.get("bio"),
        "interests": [i.strip() for i in request.form.get("interests", "").split(',') if i.strip()],
        "photos": updated_photos,
        "gender": request.form.get("gender"),
        "aesthetics": request.form.get("aesthetics_custom") or request.form.get("aesthetics"),
        "relationship": request.form.get("relationship_custom") or request.form.get("relationship"),
        "fun_option": request.form.get("fun_option_custom") or request.form.get("fun_option"),
        "hangout": request.form.get("hangout_custom") or request.form.get("hangout"),
        "looking_for": request.form.get("looking_for"),
    }


    supabase.table("profiles").update(update_data).eq("id", user_id).execute()

    flash("Profile updated successfully!", "success")
    return redirect(url_for('dashboard'))




@app.route('/add_photos', methods=['POST'])
def add_photos():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    uploaded_photos = request.files.getlist("photos")
    if not uploaded_photos:
        flash('No photos selected.', 'error')
        return redirect(url_for('dashboard'))
    res_profile = supabase.table("profiles").select("photos").eq("id", user_id).execute()
    existing_photos = res_profile.data[0]['photos'] if res_profile.data and res_profile.data[0]['photos'] else []
    new_photo_urls = []
    for photo in uploaded_photos:
        if photo and photo.filename:
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
            file_name = f"{user_id}_{timestamp}_{photo.filename}"
            temp_path = os.path.join("uploads", file_name)
            os.makedirs("uploads", exist_ok=True)
            photo.save(temp_path)
            with open(temp_path, "rb") as f:
                supabase.storage.from_("profile_photos").upload(
                    file_name, f, {"upsert": "true"}
                )
            public_url = supabase.storage.from_("profile_photos").get_public_url(file_name)
            new_photo_urls.append(public_url)
            os.remove(temp_path)
    if new_photo_urls:
        updated_photos = existing_photos + new_photo_urls
        supabase.table("profiles").update({"photos": updated_photos}).eq("id", user_id).execute()
        flash('Photos uploaded successfully!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/delete_photo', methods=['POST'])
def delete_photo():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    photo_url = request.form.get('photo_url')

    if not photo_url:
        flash('No photo URL provided.', 'error')
        return redirect(url_for('dashboard'))

    # Step 1: Get the current list of photos from the database
    res = supabase.table("profiles").select("photos").eq("id", user_id).execute()
    existing_photos = res.data[0]['photos'] if res.data and res.data[0]['photos'] else []

    # Step 2: Remove the URL from the list
    if photo_url in existing_photos:
        existing_photos.remove(photo_url)

        # Step 3: Update the database with the new list
        supabase.table("profiles").update({"photos": existing_photos}).eq("id", user_id).execute()

        # Step 4: Delete the file from Supabase Storage
        # The filename is the part of the URL after the bucket path.
        # e.g., 'profile_photos/user_123.jpg'
        # Split the URL to get the file name
        path_in_storage = '/'.join(photo_url.split('/')[-2:])
        supabase.storage.from_("profile_photos").remove([path_in_storage])

        flash('Photo deleted successfully.', 'success')
    else:
        flash('Photo not found.', 'error')
    
    return redirect(url_for('dashboard'))


@app.route('/see-other', methods=['GET', 'POST'])
def see_other():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    current_user_id = session['user_id']

    # Get current user's vip status
    res_user = supabase.table("profiles").select("vip").eq("id", current_user_id).execute()
    if not res_user.data:
        flash("User profile not found.", "danger")
        return redirect(url_for('dashboard'))

    is_vip = res_user.data[0].get("vip", False)

    if not is_vip:
        flash("Restricted for VIP users only.", "warning")
        return redirect(url_for('dashboard'))

    # --- Search support ---
    search_query = request.args.get("q", "").strip()

    query = supabase.table("profiles").select("*").neq("id", current_user_id)
    if search_query:
        # ilike = case-insensitive LIKE in PostgREST
        query = query.ilike("name", f"%{search_query}%")

    res_profiles = query.execute()
    profiles = res_profiles.data if res_profiles.data else []
    session['back_url'] = url_for('see_other')

    return render_template("see_other.html", users=profiles, q=search_query)



@app.route('/profile/<user_id>')
def view_profile(user_id):
    res = supabase.table("profiles").select("*").eq("id", user_id).execute()
    profile = res.data[0] if res.data else None
    if not profile:
        return "Profile not found", 404
    if profile and profile['photos'] is None:
        profile['photos'] = []

    # Like info
    messages_count = get_chat_partners_count(user_id)
    match_count = get_match_count(user_id)
    likes_count = get_likes_count(user_id)
    already_liked = False
    if 'user_id' in session:
        already_liked = has_liked(session['user_id'], user_id)

    session['back_url'] = url_for('view_profile', user_id=user_id)
    return render_template("view_profile.html", profile=profile,
                           likes_count=likes_count, already_liked=already_liked,match_count=match_count,messages_count=messages_count)


def is_blocked(blocker_id, blocked_id):
    res = (
        supabase.table("blocked_users")
        .select("id")
        .eq("blocker_id", blocker_id)
        .eq("blocked_id", blocked_id)
        .execute()
    )
    return len(res.data) > 0


@app.route('/block/<user_id>', methods=['POST'])
def block_user(user_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    current_user = session['user_id']

    # Get the name of the user being blocked
    res = supabase.table("profiles").select("name").eq("id", user_id).execute()
    blocked_name = res.data[0]['name'] if res.data else "User"

    supabase.table("blocked_users").insert({
        "blocker_id": current_user,
        "blocked_id": user_id
    }).execute()

    flash(f"{blocked_name} has been blocked successfully.", "success")
    return redirect(url_for('chat', receiver_id=user_id))



@app.route('/unblock/<user_id>', methods=['POST'])
def unblock_user(user_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    current_user = session['user_id']

    # Get the name of the user being unblocked
    res = supabase.table("profiles").select("name").eq("id", user_id).execute()
    unblocked_name = res.data[0]['name'] if res.data else "User"

    supabase.table("blocked_users") \
        .delete() \
        .eq("blocker_id", current_user) \
        .eq("blocked_id", user_id) \
        .execute()

    flash(f"{unblocked_name} has been unblocked successfully.", "success")
    return redirect(url_for('chat', receiver_id=user_id))


@app.route('/chat/<receiver_id>', methods=['GET'])
def chat(receiver_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    sender_id = session['user_id']

    # Check block statuses
    blocked_by_me = is_blocked(sender_id, receiver_id)
    blocked_by_them = is_blocked(receiver_id, sender_id)

    # Get receiver profile
    receiver_res = supabase.table("profiles").select("name, photos").eq("id", receiver_id).execute()
    if receiver_res.data:
        receiver_data = receiver_res.data[0]
        receiver_name = receiver_data['name']
        photos = receiver_data.get('photos') or []
        receiver_profile_url = photos[0] if photos else "https://images.pexels.com/photos/220453/pexels-photo-220453.jpeg?auto=compress&cs=tinysrgb&w=400&h=400&fit=crop&crop=face"
    else:
        receiver_name = 'User'
        receiver_profile_url = "https://images.pexels.com/photos/220453/pexels-photo-220453.jpeg?auto=compress&cs=tinysrgb&w=400&h=400&fit=crop&crop=face"

    # Mark messages as read
    mark_messages_as_read(receiver_id=sender_id, sender_id=receiver_id)

    # Fetch last 50 messages
    query = (
        f"and(sender_id.eq.\"{sender_id}\",receiver_id.eq.\"{receiver_id}\"),"
        f"and(sender_id.eq.\"{receiver_id}\",receiver_id.eq.\"{sender_id}\")"
    )
    res = supabase.table("messages").select("*").or_(query).order("created_at").limit(50).execute()
    messages = res.data if res.data else []

    back_url = session.get('back_url', url_for('dashboard'))

    return render_template(
        "chat.html",
        messages=messages,
        receiver_id=receiver_id,
        receiver_name=receiver_name,
        receiver_profile_url=receiver_profile_url,
        sender_id=sender_id,
        blocked_by_me=blocked_by_me,
        blocked_by_them=blocked_by_them,
        SUPABASE_URL="https://uccwrkpdkheliitelkag.supabase.co",
        SUPABASE_KEY=SUPABASE_KEY,
        back_url=back_url
    )

@app.route('/read_messages/<sender_id>', methods=['POST'])
def read_messages(sender_id):
    if 'user_id' not in session:
        return jsonify({"success": False}), 401

    receiver_id = session['user_id']  # logged-in user
    mark_messages_as_read(receiver_id=receiver_id, sender_id=sender_id)

    return jsonify({"success": True})

@app.route('/chat_list')
def chat_list():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    current_user_id = session['user_id']
    res_messages = (
        supabase.table("messages")
        .select("sender_id, receiver_id, content, created_at")
        .or_(f"sender_id.eq.{current_user_id},receiver_id.eq.{current_user_id}")
        .order("created_at", desc=True)
        .execute()
    )
    all_messages = res_messages.data if res_messages.data else []
    chat_partners = {}
    for message in all_messages:
        partner_id = message['sender_id'] if message['sender_id'] != current_user_id else message['receiver_id']
        if partner_id not in chat_partners:
            chat_partners[partner_id] = {
                'id': partner_id,
                'last_message': message['content'],
                'created_at': message['created_at']
            }
    chat_partner_ids = list(chat_partners.keys())
    chat_users = []
    if chat_partner_ids:
        res_profiles = supabase.table("profiles").select("*").in_("id", chat_partner_ids).execute()
        profiles_map = {profile['id']: profile for profile in res_profiles.data}
        for partner_id, partner_data in chat_partners.items():
            if partner_id in profiles_map:
                profile = profiles_map[partner_id]
                profile['last_message'] = partner_data['last_message']
                profile['last_message_date'] = partner_data['created_at']
                chat_users.append(profile)
        chat_users.sort(key=lambda x: x['last_message_date'], reverse=True)
    unread_counts = get_unread_count_per_chat(current_user_id)
    session['back_url'] = url_for('chat_list')
    return render_template("chat_list.html", users=chat_users, unread_counts=unread_counts)



@app.route('/like/<string:liked_id>', methods=['POST'])
def like(liked_id):
    if 'user_id' not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 401
    
    liker_id = session['user_id']
    if liker_id == liked_id:
        return jsonify({"success": False, "error": "Cannot like yourself"}), 400

    if has_liked(liker_id, liked_id):
        # Already liked → remove the like (unlike)
        remove_like(liker_id, liked_id)
        return jsonify({
            "success": True,
            "match": False,
            "unliked": True,
            "likes_count": get_likes_count(liked_id)
        })
    else:
        # Not liked → add like
        add_like(liker_id, liked_id)
        # Check for mutual match
        has_liked_back = has_liked(liked_id, liker_id)
        match_data = {}
        if has_liked_back:
            res_current = supabase.table("profiles").select("photos").eq("id", liker_id).execute()
            current_user_pic = res_current.data[0]['photos'][0] if res_current.data and res_current.data[0]['photos'] else None

            res_matched = supabase.table("profiles").select("photos").eq("id", liked_id).execute()
            matched_user_pic = res_matched.data[0]['photos'][0] if res_matched.data and res_matched.data[0]['photos'] else None

            match_data = {
                "match": True,
                "current_user_pic": current_user_pic,
                "matched_user_pic": matched_user_pic
            }
        else:
            match_data = {"match": False}

        return jsonify({
            "success": True,
            **match_data,
            "unliked": False,
            "likes_count": get_likes_count(liked_id)
        })
    

@app.route('/like2/<string:liked_id>', methods=['POST'])
def like2(liked_id):
    if 'user_id' not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 401
    
    liker_id = session['user_id']
    if liker_id == liked_id:
        return jsonify({"success": False, "error": "Cannot like yourself"}), 400

    has_already_liked = has_liked(liker_id, liked_id)

    # If not already liked, add the like first
    if not has_already_liked:
        add_like(liker_id, liked_id)

    # Check for mutual match regardless of whether a new like was added or not
    has_liked_back = has_liked(liked_id, liker_id)
    match_data = {}
    if has_liked_back:
        res_current = supabase.table("profiles").select("photos").eq("id", liker_id).execute()
        current_user_pic = res_current.data[0]['photos'][0] if res_current.data and res_current.data[0]['photos'] else None

        res_matched = supabase.table("profiles").select("photos").eq("id", liked_id).execute()
        matched_user_pic = res_matched.data[0]['photos'][0] if res_matched.data and res_matched.data[0]['photos'] else None

        match_data = {
            "match": True,
            "current_user_pic": current_user_pic,
            "matched_user_pic": matched_user_pic
        }
    else:
        match_data = {"match": False}

    return jsonify({
        "success": True,
        **match_data,
        "unliked": False, # 'unliked' is always false as we never remove a like
        "likes_count": get_likes_count(liked_id)
    })


'''
@app.route('/start-matching')
def start_matching():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    current_user_id = session['user_id']

    # 1. Get current user's gender
    current_user_profile_res = supabase.table("profiles").select("gender").eq("id", current_user_id).execute()
    current_user_gender = current_user_profile_res.data[0]['gender'] if current_user_profile_res.data else None

    if not current_user_gender:
        flash('Could not find your profile to start matching.', 'error')
        return redirect(url_for('dashboard'))

    opposite_gender = 'female' if current_user_gender.lower() == 'male' else 'male'

    # 2. Fetch potential matches
    potential_matches_res = supabase.table("profiles") \
        .select("id") \
        .eq("gender", opposite_gender) \
        .neq("id", current_user_id) \
        .execute()

    potential_match_ids = [p['id'] for p in potential_matches_res.data]

    if not potential_match_ids:
        flash('No new profiles to match with. Try again later!', 'info')
        return redirect(url_for('dashboard'))

    # 3. Randomly pick one
    matched_id = random.choice(potential_match_ids)

    now = datetime.now(timezone.utc).isoformat()

    # 4. Save in match_history only if unique
    existing_match = supabase.table("match_history") \
        .select("id") \
        .eq("user_id", current_user_id) \
        .eq("matched_id", matched_id) \
        .execute()

    if not existing_match.data:
        supabase.table("match_history").insert({
            "user_id": current_user_id,
            "matched_id": matched_id,
            "created_at": now
        }).execute()

    # 5. Always log in match_activity (so duplicates appear in activity)
    supabase.table("match_activity").insert({
        "user_id": current_user_id,
        "matched_id": matched_id,
        "created_at": now
    }).execute()

    return redirect(url_for('view_profile', user_id=matched_id))

'''

@app.route('/matching')
def matching():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('matching.html')

@app.route('/next-profile')
def next_profile():
    if 'user_id' not in session:
        return jsonify({"error": "Not logged in"}), 401

    current_user_id = session['user_id']

    # Get current user’s gender
    current_user_profile_res = supabase.table("profiles").select("gender").eq("id", current_user_id).execute()
    current_user_gender = current_user_profile_res.data[0]['gender'] if current_user_profile_res.data else None
    if not current_user_gender:
        return jsonify({"error": "Profile not found"}), 404

    gender = current_user_gender.lower()

    if gender == "male":
        opposite_genders = ["female", "non-binary", "prefer-not-to-say"]
    elif gender == "female":
        opposite_genders = ["male", "non-binary", "prefer-not-to-say"]
    elif gender == "non-binary":
        opposite_genders = ["male", "female", "prefer-not-to-say"]
    elif gender == "prefer-not-to-say":
        opposite_genders = ["male", "female", "non-binary"]
    else:
        opposite_genders = ["male", "female", "non-binary", "prefer-not-to-say"]  # fallback

    # Fetch potential matches
    potential_matches_res = (
        supabase.table("profiles")
        .select("id, name, age, bio, photos")
        .in_("gender", opposite_genders)     # ✅ fix here
        .neq("id", current_user_id)
        .execute()
    )

    potential_matches = potential_matches_res.data
    if not potential_matches:
        return jsonify({"error": "No matches found"}), 404

    matched_profile = random.choice(potential_matches)

    now = datetime.now(timezone.utc).isoformat()

    # Log in match_history (if unique)
    existing_match = (
        supabase.table("match_history")
        .select("id")
        .eq("user_id", current_user_id)
        .eq("matched_id", matched_profile['id'])
        .execute()
    )

    if not existing_match.data:
        supabase.table("match_history").insert({
            "user_id": current_user_id,
            "matched_id": matched_profile['id'],
            "created_at": now
        }).execute()

    # Always log in match_activity
    supabase.table("match_activity").insert({
        "user_id": current_user_id,
        "matched_id": matched_profile['id'],
        "created_at": now
    }).execute()

    # Handle profile picture
    if matched_profile.get("photos") and len(matched_profile["photos"]) > 0:
        matched_profile["profile_picture"] = matched_profile["photos"][0]
    else:
        matched_profile["profile_picture"] = "/static/default-pic.png"

    return jsonify(matched_profile)

@app.route("/get_matches")
def get_matches():
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session["user_id"]

    # Step 1: Get matches where matched_id = current user
    response = supabase.table("match_history") \
        .select("*") \
        .eq("matched_id", user_id) \
        .execute()

    match_rows = response.data

    # Step 2: Collect the IDs of the "other" users (who initiated the match)
    other_user_ids = [row["user_id"] for row in match_rows]

    # Step 3: Fetch profile details of the other users
    profiles = []
    if other_user_ids:
        profiles_resp = supabase.table("profiles") \
            .select("id, name, photos") \
            .in_("id", other_user_ids) \
            .execute()
        profiles = profiles_resp.data

    return jsonify(profiles)



@app.route('/get_message_partners')
def get_message_partners():
    if 'user_id' not in session:
        return jsonify([])

    current_user_id = session['user_id']

    res_messages = (
        supabase.table("messages")
        .select("sender_id, receiver_id")
        .or_(f"sender_id.eq.{current_user_id},receiver_id.eq.{current_user_id}")
        .execute()
    )
    all_messages = res_messages.data if res_messages.data else []

    partner_ids = set()
    for msg in all_messages:
        partner_ids.add(msg['sender_id'] if msg['sender_id'] != current_user_id else msg['receiver_id'])

    res_profiles = supabase.table("profiles").select("id, name, photos").in_("id", list(partner_ids)).execute()
    return jsonify(res_profiles.data)


@app.route('/get_likes')
def get_likes():
    if 'user_id' not in session:
        return jsonify([])

    current_user_id = session['user_id']

    res_likes = (
        supabase.table("likes")
        .select("liker_id, liked_id")
        .eq("liked_id", current_user_id)
        .execute()
    )
    likes = res_likes.data if res_likes.data else []

    liker_ids = [l['liker_id'] for l in likes]

    res_profiles = supabase.table("profiles").select("id, name, photos").in_("id", liker_ids).execute()
    return jsonify(res_profiles.data)


@app.route('/logout')
def logout():
    # Clear all session data
    session.clear()
    # Force Flask to issue a new session ID
    session.modified = True
    return redirect(url_for('landing'))

if __name__ == '__main__':
    app.run(debug=True)
