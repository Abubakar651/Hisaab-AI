/**
 * HisaabAI — Client-Side Bilingual i18n Engine (Urdu & English)
 * - Zero external dependencies, CSP compliant (no eval / no inline).
 * - Default: Urdu (lang="ur", dir="rtl") on first visit.
 * - Stores language preference in localStorage under key 'hisaab_ui_lang'.
 * - Western Arabic numerals (1, 2, 3) with PKR used across both languages.
 * - Provides window.HisaabI18n and window.t(key, params).
 */

(function () {
  'use strict';

  const STORAGE_KEY = 'hisaab_ui_lang';

  const DICTIONARIES = {
    ur: {
      // Header & Navigation (Always in English per design requirement)
      'header.title': 'HisaabAI',
      'header.chat': 'Chat',
      'header.split': 'Split',
      'header.ledger': 'Ledger',
      'header.sign_in': 'Sign In',
      'header.guest': 'Guest',
      'header.not_signed_in': 'Not signed in',
      'header.export_csv': 'Export CSV',
      'header.switch_account': 'Switch Account',
      'header.sign_out': 'Sign Out',
      'lang_toggle.label': 'English',
      'lang_toggle.title': 'Switch to English / انگریزی زبان منتخب کریں',

      // Mobile Tabs
      'tab.chat': 'چیٹ اور آواز',
      'tab.ledger': 'لائیو کھاتہ',

      // Chat Pane
      'chat.active_session': 'ایکٹو سیشن:',
      'chat.clear_screen': 'اسکرین صاف کریں',
      'chat.welcome_title': 'السلام علیکم! حساب اے آئی (HisaabAI) میں خوش آمدید۔',
      'chat.welcome_sub': 'اپنے تمام روزمرہ اخراجات بول کر یا لکھ کر باآسانی درج کریں:',
      'chat.welcome_ur': 'اردو:',
      'chat.welcome_ur_eg': '"گھر کے راشن پر 2500 اور بجلی کے بل پر 8000 خرچ ہوئے"',
      'chat.welcome_en': 'انگریزی:',
      'chat.welcome_en_eg': '"Spent 3,400 on weekly groceries and 1,200 on fuel"',
      'chat.welcome_ask': 'حساب پوچھیں:',
      'chat.welcome_ask_eg': '"میرا اس مہینے کا کل خرچ کتنا ہے؟"، "راشن پر کتنا خرچ ہوا؟"',
      'chat.chip_month': 'اس مہینے کا خرچہ',
      'chat.chip_month_prompt': 'اس مہینے کا میرا کل خرچہ کتنا ہے؟',
      'chat.chip_3days': 'پچھلے ۳ دن',
      'chat.chip_3days_prompt': 'پچھلے ۳ دن میں کتنا خرچ ہوا؟',
      'chat.chip_galla': 'آج کا گلہ',
      'chat.chip_galla_prompt': 'آج کا گلہ اور بکری بتائیں',
      'chat.chip_udhaar': 'ادھار کھاتہ',
      'chat.chip_udhaar_prompt': 'ادھار کھاتہ دکھائیں کس سے پیسے لینے ہیں؟',
      'chat.chip_budget': 'بجٹ سیٹ کریں',
      'chat.chip_budget_prompt': 'میرا اس مہینے کا بجٹ سیٹ کریں',
      'chat.chip_undo': 'آخری اندراج مٹائیں',
      'chat.chip_undo_prompt': 'میرا آخری اندراج مٹا دیں',
      'chat.recording': 'آواز ریکارڈ ہو رہی ہے...',
      'chat.cancel': 'منسوخ کریں',
      'chat.placeholder': 'اردو یا انگریزی میں بولیں یا لکھیں...',
      'chat.transcribing_placeholder': 'آواز تحریر میں تبدیل ہو رہی ہے...',
      'chat.tip': 'ٹپ: کسی بھی دکان، مارکیٹ، خریداری یا بل کی رقم بولیں یا لکھیں (مثلاً راشن، پیٹرول، کھانا، بجلی کا بل یا ادویات)۔',
      'chat.loading': 'حساب کتاب دیکھا جا رہا ہے...',
      'chat.voice_tag': 'وائس نوٹ، تحریر شدہ',
      'chat.error_network': 'سرور سے رابطہ نہیں ہو سکا۔ انٹرنیٹ کنکشن چیک کریں اور دوبارہ کوشش کریں۔',
      'chat.error_busy': 'اس وقت سسٹم پر زیادہ رش ہے۔ براہ کرم چند سیکنڈ بعد دوبارہ کوشش کریں۔',

      // Ledger Pane
      'ledger.total_spend': 'اس مہینے کا کل خرچ',
      'ledger.refresh': 'تازہ کریں',
      'ledger.purchases': 'خریداری',
      'ledger.top_store': 'پسندیدہ دکان:',
      'ledger.not_recorded': 'درج نہیں',
      'ledger.no_tx_this_month': 'اس مہینے کا کوئی خرچہ درج نہیں ہے',
      'ledger.tx_count': '{count} خریداری',
      'ledger.budgets_title': 'ماہانہ بجٹ',
      'ledger.budgets_empty': 'کوئی بجٹ سیٹ نہیں ہے۔ نیچے بجٹ شامل کریں یا لکھیں "میرا ماہانہ بجٹ 50000 ہے"۔',
      'ledger.where_it_went': 'کہاں کتنا خرچ ہوا',
      'ledger.no_category_spend': 'اس مہینے کا کوئی خرچہ درج نہیں ہے۔',
      'ledger.trend_title': 'روزانہ اخراجات (گزشتہ ۳۰ دن)',
      'ledger.stores_title': 'دکانوں کے لحاظ سے تفصیل',
      'ledger.stores_empty': 'ابھی تک کوئی دکان ریکارڈ میں نہیں۔',
      'ledger.payment_split_title': 'ادائیگی کا طریقہ (نقد بمقابلہ ڈیجیٹل)',
      'ledger.payment_split_empty': 'ادائیگی کا کوئی ڈیٹا درج نہیں۔',
      'ledger.cash': 'نقد',
      'ledger.digital': 'ڈیجیٹل',
      'ledger.recurring_title': 'ماہانہ بل اور ضروری واجبات',
      'ledger.recurring_empty': 'کوئی بل شامل نہیں ہے۔ نیچے کرایہ، بجلی، اسکول فیس وغیرہ درج کریں۔',
      'ledger.recent_tx_title': 'حالیہ اخراجات کی تفصیل',
      'ledger.recent_tx_empty': 'ابھی تک کوئی خرچہ ریکارڈ نہیں ہوا۔<br>چیٹ میں اپنا پہلا خرچہ بولیں یا لکھیں!',

      // Forms & Buttons
      'btn.save': 'محفوظ کریں',
      'btn.add': 'شامل کریں',
      'btn.confirm': 'تصدیق کریں',
      'btn.cancel': 'منسوخ کریں',
      'btn.mark_paid': 'ادا شدہ کریں',
      'btn.remove': 'ختم کریں',
      'bill.paid': 'ادا شدہ',
      'bill.unpaid': 'واجب الادا',
      'bill.pending': 'زیر التواء',
      'bill.due_day': 'آخری تاریخ',
      'budget.overall': 'کل بجٹ',
      'budget.groceries': 'راشن و گروسری',
      'budget.dairy': 'ڈیری و بیکری',
      'budget.fuel': 'پیٹرول و ایندھن',
      'budget.dining': 'کھانا پینا',
      'budget.utilities': 'بجلی، گیس و پانی کے بلز',
      'budget.healthcare': 'ادویات و علاج',
      'budget.clothing': 'لباس و خریداری',
      'budget.rent': 'کرایہ دکان یا مکان',
      'budget.education': 'تعلیم و فیس',
      'budget.other': 'دیگر اخراجات',
      'budget.custom': '✍️ اپنی مرضی کا بجٹ نام',
      'budget.custom_placeholder': 'بجٹ کا کسٹم نام لکھیں (مثلاً جم، سفر، مرمت)',
      'budget.amount_placeholder': 'بجٹ رقم (PKR)',
      'budget.over': 'بجٹ سے PKR {amount} زیادہ خرچ ہو گئے',
      'budget.over_by': 'بجٹ سے PKR {amount} زیادہ خرچ ہو گئے',
      'budget.warning': 'اس مہینے صرف PKR {amount} باقی ہیں',
      'budget.remaining': 'اس مہینے صرف PKR {amount} باقی ہیں',
      'bill.name_placeholder': 'بل کا نام (مثلاً مکان کا کرایہ، بجلی بل)',
      'bill.amount_placeholder': 'رقم (PKR)',
      'bill.day_placeholder': 'تاریخ (1-28)',
      'placeholder.budget_custom_name': 'بجٹ کا کسٹم نام لکھیں (مثلاً جم، سفر، مرمت)',
      'placeholder.monthly_budget_pkr': 'ماہانہ بجٹ رقم (PKR)',
      'placeholder.bill_title': 'بل کا نام (مثلاً مکان کا کرایہ، بجلی بل)',
      'placeholder.bill_amount': 'رقم (PKR)',
      'placeholder.bill_due_day': 'تاریخ (1-28)',

      // Large Expense Confirmation Banner
      'confirm.badge': '⚠️ تصدیق درکار ہے (Large Expense Confirmation)',
      'confirm.store': 'دکان:',
      'confirm.amount': 'رقم:',
      'confirm.items': 'اشیاء:',
      'confirm.btn_yes': 'تصدیق کریں (Confirm)',
      'confirm.btn_no': 'منسوخ کریں (Cancel)',

      // Modals (Auth & Mic)
      'auth.title': 'لاگ ان HisaabAI',
      'auth.subtitle': 'کثیر لسانی آواز اور تحریر کا کھاتہ',
      'auth.desc': 'اپنے اخراجات کو محفوظ رکھنے اور کسی بھی ڈیوائس پر سنک کرنے کے لیے لاگ ان کریں یا نیا اکاؤنٹ بنائیں۔',
      'auth.tab_signin': 'لاگ ان',
      'auth.tab_signup': 'نیا اکاؤنٹ',
      'auth.name': 'مکمل نام',
      'auth.name_placeholder': 'اپنا نام درج کریں (مثلاً ابوبکر اقبال)',
      'auth.email': 'ای میل ایڈریس',
      'auth.password': 'پاس ورڈ',
      'auth.password_placeholder': 'کم از کم 8 ہندسے',
      'auth.forgot_password': 'پاس ورڈ بھول گئے؟',
      'auth.or': 'یا',
      'auth.google': 'گوگل کے ساتھ جاری رکھیں',
      'auth.notice': 'محفوظ و نجی • گوگل فائر بیس توثیق',
      'auth.verify_title': 'اپنے ای میل کی تصدیق کریں',
      'auth.verify_desc': 'آپ کے ای میل پر تصدیقی لنک بھیج دیا گیا ہے۔ اکاؤنٹ فعال کرنے کے لیے لنک پر کلک کریں۔',
      'auth.verify_done': 'میں نے ای میل کی تصدیق کر لی ہے',
      'auth.verify_resend': 'دوبارہ تصدیقی ای میل بھیجیں',
      'auth.verify_different': 'دوسرا اکاؤنٹ استعمال کریں یا لاگ آؤٹ',
      'mic.title': 'مائیکروفون بلاک ہے',
      'mic.subtitle': 'براؤزر میں مائیک کی اجازت بلاک ہے',
      'mic.dismiss': 'بند کریں',
      'mic.retry': 'دوبارہ کوشش',

      // Toasts
      'toast.chat_cleared': 'اسکرین سے چیٹ صاف کر دی گئی',
      'toast.ledger_refreshed': 'کھاتہ تازہ کر دیا گیا',
      'toast.csv_downloaded': 'CSV فائل ڈاؤن لوڈ ہو گئی',
      'toast.mic_transcribing': 'آواز تحریر میں تبدیل ہو رہی ہے...',
      'toast.speech_transcribed': '🎤 آواز چیٹ باکس میں تحریر ہو گئی! جائزہ لیں اور Send دبائیں۔',
      'toast.no_speech': 'کوئی آواز ریکارڈ نہیں ہوئی۔ مائیک کے قریب بولیں۔',
      'toast.stt_failed': 'آواز تحریر کرنے میں مسئلہ پیش آیا۔ دوبارہ کوشش کریں۔',
      'toast.recording_cancelled': 'ریکارڈنگ منسوخ کر دی گئی',
      'toast.expense_confirmed': 'خرچہ کامیابی سے درج ہو گیا',
      'toast.expense_cancelled': 'خرچہ منسوخ کر دیا گیا',
      'toast.conflict_409': 'پہلے ہی تصدیق، منسوخ یا ایکسپائر ہو چکا ہے۔',
      'toast.budget_saved': 'بجٹ محفوظ ہو گیا',
      'toast.budget_name_required': 'براہ کرم کسٹم بجٹ کا نام درج کریں',
      'toast.recurring_saved': 'بل محفوظ ہو گیا',
      'toast.tx_deleted': 'اندراج #{id} مٹا دیا گیا',
      'toast.signed_in': 'کامیابی سے لاگ ان ہو گئے!',
      'toast.signed_out': 'لاگ آؤٹ ہو گئے',
      'toast.account_created': 'اکاؤنٹ بن گیا! تصدیق کے لیے اپنا ای میل چیک کریں۔',

      // Shopkeeper: Galla & Udhaar Khata
      'galla.title': 'آج کا گلہ (روزانہ بکری اور خرچہ)',
      'galla.today_sales': 'آج کی بکری (In)',
      'galla.today_expenses': 'آج کا خرچہ (Out)',
      'galla.today_net': 'خالص گلہ (Net)',
      'galla.cash_short': 'نقد',
      'galla.digital_short': 'ڈیجیٹل',
      'udhaar.title': 'ادھار کھاتہ (لین دین)',
      'udhaar.lene_hain': 'لینے ہیں:',
      'udhaar.dene_hain': 'دینے ہیں:',
      'udhaar.badge_lene': 'لینے ہیں (وصولی)',
      'udhaar.badge_dene': 'دینے ہیں (ادائیگی)',
      'udhaar.empty': 'کوئی ادھار درج نہیں ہے۔ نیچے شامل کریں یا چیٹ میں کہیں "علی کو 1500 ادھار دیا"۔',
      'udhaar.person_placeholder': 'نام (مثلاً علی بھائی، طارق کریانہ)',
      'udhaar.amount_placeholder': 'رقم (PKR)',
      'udhaar.opt_receivable': 'لینے ہیں (دیے)',
      'udhaar.opt_payable': 'دینے ہیں (لیے)',
      'udhaar.notes_placeholder': 'تفصیل / فون نمبر (اختیاری)',
      'udhaar.btn_settle': 'وصول / چکتا',
      'udhaar.btn_whatsapp': 'واٹس ایپ یاد دہانی',
      'tx.sale_badge': 'بکری (Sale)',
      'toast.udhaar_saved': 'ادھار کھاتہ اپ ڈیٹ ہو گیا',
      'toast.udhaar_settled': 'ادھار چکتا کر دیا گیا',
      'toast.udhaar_deleted': 'ادھار کا اندراج مٹا دیا گیا'
    },

    en: {
      // Header & Navigation
      'header.title': 'HisaabAI',
      'header.chat': 'Chat',
      'header.split': 'Split',
      'header.ledger': 'Ledger',
      'header.sign_in': 'Sign In',
      'header.guest': 'Guest',
      'header.not_signed_in': 'Not signed in',
      'header.export_csv': 'Export CSV',
      'header.switch_account': 'Switch Account',
      'header.sign_out': 'Sign Out',
      'lang_toggle.label': 'اردو',
      'lang_toggle.title': 'Switch to Urdu / اردو زبان منتخب کریں',

      // Mobile Tabs
      'tab.chat': 'Chat & Voice',
      'tab.ledger': 'Live Ledger',

      // Chat Pane
      'chat.active_session': 'Active Session:',
      'chat.clear_screen': 'Clear Screen',
      'chat.welcome_title': 'Welcome to HisaabAI!',
      'chat.welcome_sub': 'Track your daily expenses easily by speaking or typing:',
      'chat.welcome_ur': 'Urdu:',
      'chat.welcome_ur_eg': '"گھر کے راشن پر 2500 اور بجلی کے بل پر 8000 خرچ ہوئے"',
      'chat.welcome_en': 'English:',
      'chat.welcome_en_eg': '"Spent 3,400 on weekly groceries and 1,200 on fuel"',
      'chat.welcome_ask': 'Ask your ledger:',
      'chat.welcome_ask_eg': '"What is my total spend this month?", "How much on groceries?"',
      'chat.chip_month': 'This month\'s spend',
      'chat.chip_month_prompt': 'What is my total spend this month?',
      'chat.chip_3days': 'Last 3 days',
      'chat.chip_3days_prompt': 'What did I spend in the last 3 days?',
      'chat.chip_galla': 'Today\'s Galla',
      'chat.chip_galla_prompt': 'Show today\'s sales and cash drawer summary',
      'chat.chip_udhaar': 'Udhaar Khata',
      'chat.chip_udhaar_prompt': 'Show my udhaar khata: who owes me money and who do I owe?',
      'chat.chip_budget': 'Set monthly budget',
      'chat.chip_budget_prompt': 'I want to set my monthly budget',
      'chat.chip_undo': 'Delete last entry',
      'chat.chip_undo_prompt': 'Delete my last entry',
      'chat.recording': 'Recording audio...',
      'chat.cancel': 'Cancel',
      'chat.placeholder': 'Speak or type in Urdu or English...',
      'chat.transcribing_placeholder': 'Converting speech to text...',
      'chat.tip': 'Tip: Speak or type expenses from any store, market or bill (e.g. groceries, fuel, dining, electricity, healthcare).',
      'chat.loading': 'Calculating ledger...',
      'chat.voice_tag': 'Voice note, transcribed',
      'chat.error_network': 'Unable to connect to server. Please check your internet connection and try again.',
      'chat.error_busy': 'The assistant is temporarily busy with high request volume. Please try again in a few moments.',

      // Ledger Pane
      'ledger.total_spend': 'Total Spend This Month',
      'ledger.refresh': 'Refresh',
      'ledger.purchases': 'purchases',
      'ledger.top_store': 'Top Store:',
      'ledger.not_recorded': 'None',
      'ledger.no_tx_this_month': 'No expenses recorded this month',
      'ledger.tx_count': '{count} purchases',
      'ledger.budgets_title': 'Monthly Budgets',
      'ledger.budgets_empty': 'No budgets set. Add a budget below or type "Set my monthly budget to 50000".',
      'ledger.where_it_went': 'Where It Went',
      'ledger.no_category_spend': 'No category expenses recorded this month.',
      'ledger.trend_title': 'Daily Expenses (Last 30 Days)',
      'ledger.stores_title': 'Spending by Store',
      'ledger.stores_empty': 'No stores recorded yet.',
      'ledger.payment_split_title': 'Payment Method (Cash vs Digital)',
      'ledger.payment_split_empty': 'No payment data recorded yet.',
      'ledger.cash': 'Cash',
      'ledger.digital': 'Digital',
      'ledger.recurring_title': 'Recurring Monthly Bills & Obligations',
      'ledger.recurring_empty': 'No bills added. Add rent, electricity, school fees, etc. below.',
      'ledger.recent_tx_title': 'Recent Transactions',
      'ledger.recent_tx_empty': 'No expenses recorded yet.<br>Speak or type your first expense in chat!',

      // Forms & Buttons
      'btn.save': 'Save',
      'btn.add': 'Add',
      'btn.confirm': 'Confirm',
      'btn.cancel': 'Cancel',
      'btn.mark_paid': 'Mark as Paid',
      'btn.remove': 'Remove',
      'bill.paid': 'Paid',
      'bill.unpaid': 'Unpaid',
      'bill.pending': 'Pending',
      'bill.due_day': 'Due Day',
      'budget.overall': 'Overall Budget (Total)',
      'budget.groceries': 'Groceries',
      'budget.dairy': 'Dairy & Bakery',
      'budget.fuel': 'Fuel & Petrol',
      'budget.dining': 'Dining & Food',
      'budget.utilities': 'Utility Bills',
      'budget.healthcare': 'Healthcare & Medicine',
      'budget.clothing': 'Clothing & Shopping',
      'budget.rent': 'House Rent',
      'budget.education': 'Education & School Fees',
      'budget.other': 'Other Expenses',
      'budget.custom': '✍️ Custom Name...',
      'budget.custom_placeholder': 'Enter custom budget name (e.g. Gym, Travel, Repairs)',
      'budget.amount_placeholder': 'Budget Limit (PKR)',
      'budget.over': 'PKR {amount} over budget',
      'budget.over_by': 'PKR {amount} over budget',
      'budget.warning': 'Only PKR {amount} remaining this month',
      'budget.remaining': 'Only PKR {amount} remaining this month',
      'bill.name_placeholder': 'Bill name (e.g. Rent, Electricity)',
      'bill.amount_placeholder': 'Amount (PKR)',
      'bill.day_placeholder': 'Due Day (1-28)',
      'placeholder.budget_custom_name': 'Enter custom budget name (e.g. Gym, Travel, Repairs)',
      'placeholder.monthly_budget_pkr': 'Monthly Budget Limit (PKR)',
      'placeholder.bill_title': 'Bill name (e.g. Rent, Electricity)',
      'placeholder.bill_amount': 'Amount (PKR)',
      'placeholder.bill_due_day': 'Due Day (1-28)',

      // Large Expense Confirmation Banner
      'confirm.badge': '⚠️ Large Expense Confirmation Required',
      'confirm.store': 'Store:',
      'confirm.amount': 'Amount:',
      'confirm.items': 'Items:',
      'confirm.btn_yes': 'Confirm',
      'confirm.btn_no': 'Cancel',

      // Modals (Auth & Mic)
      'auth.title': 'Sign In to HisaabAI',
      'auth.subtitle': 'Multilingual Voice & Text Ledger',
      'auth.desc': 'Sign in securely or create an account to automatically sync and access your expense ledger from any browser or device.',
      'auth.tab_signin': 'Sign In',
      'auth.tab_signup': 'Create Account',
      'auth.name': 'Full Name',
      'auth.name_placeholder': 'Enter your full name (e.g. Abu Bakar Iqbal)',
      'auth.email': 'Email Address',
      'auth.password': 'Password',
      'auth.password_placeholder': 'Minimum 8 characters',
      'auth.forgot_password': 'Forgot password?',
      'auth.or': 'OR',
      'auth.google': 'Continue with Google',
      'auth.notice': 'Encrypted & Isolated • Google Firebase Authentication',
      'auth.verify_title': 'Verify Your Email Address',
      'auth.verify_desc': 'A verification link has been sent to your email. Please click the link to activate your account.',
      'auth.verify_done': 'I\'ve Verified My Email',
      'auth.verify_resend': 'Resend verification email',
      'auth.verify_different': 'Use a different account / Sign out',
      'mic.title': 'Microphone Access Blocked',
      'mic.subtitle': 'Browser permission is set to "Block"',
      'mic.dismiss': 'Dismiss',
      'mic.retry': 'Retry',

      // Toasts
      'toast.chat_cleared': 'Chat history cleared from view',
      'toast.ledger_refreshed': 'Ledger refreshed',
      'toast.csv_downloaded': 'CSV export downloaded',
      'toast.mic_transcribing': 'Converting speech to text...',
      'toast.speech_transcribed': '🎤 Voice note transcribed to chat box! Review and press Send.',
      'toast.no_speech': 'No speech detected. Speak closer to the mic.',
      'toast.stt_failed': 'Failed to transcribe audio. Please try again.',
      'toast.recording_cancelled': 'Recording cancelled',
      'toast.expense_confirmed': 'Expense confirmed',
      'toast.expense_cancelled': 'Expense cancelled',
      'toast.conflict_409': 'Already confirmed, cancelled, or expired.',
      'toast.budget_saved': 'Budget saved',
      'toast.budget_name_required': 'Please enter a custom budget name',
      'toast.recurring_saved': 'Recurring bill saved',
      'toast.tx_deleted': 'Transaction #{id} deleted',
      'toast.signed_in': 'Successfully signed in!',
      'toast.signed_out': 'Signed out',
      'toast.account_created': 'Account created! Please check your email for the verification link.',

      // Shopkeeper: Galla & Udhaar Khata
      'galla.title': 'Today\'s Galla (Daily Sales & Spend)',
      'galla.today_sales': 'Today\'s Sales (In)',
      'galla.today_expenses': 'Today\'s Spend (Out)',
      'galla.today_net': 'Net Galla (Drawer)',
      'galla.cash_short': 'Cash',
      'galla.digital_short': 'Digital',
      'udhaar.title': 'Udhaar Khata (Lend & Borrow)',
      'udhaar.lene_hain': 'To Receive:',
      'udhaar.dene_hain': 'To Pay:',
      'udhaar.badge_lene': 'To Receive (Lene Hain)',
      'udhaar.badge_dene': 'To Pay (Dene Hain)',
      'udhaar.empty': 'No active udhaar entries. Add below or say in chat "Lent 1500 to Ali".',
      'udhaar.person_placeholder': 'Person / Shop Name (e.g. Ali Bhai)',
      'udhaar.amount_placeholder': 'Amount (PKR)',
      'udhaar.opt_receivable': 'To Receive (Lent / Sold on Credit)',
      'udhaar.opt_payable': 'To Pay (Borrowed / Supplier Credit)',
      'udhaar.notes_placeholder': 'Notes / Phone Number (Optional)',
      'udhaar.btn_settle': 'Mark Settled',
      'udhaar.btn_whatsapp': 'WhatsApp Reminder',
      'tx.sale_badge': 'Sale (Bikri)',
      'toast.udhaar_saved': 'Udhaar Khata updated',
      'toast.udhaar_settled': 'Udhaar marked as settled',
      'toast.udhaar_deleted': 'Udhaar entry removed'
    }
  };

  let currentLang = 'ur';
  const listeners = [];

  function readStoredLanguage() {
    try {
      if (typeof window !== 'undefined' && window.location && window.location.search) {
        const urlParam = new URLSearchParams(window.location.search).get('lang');
        if (urlParam === 'en' || urlParam === 'ur') {
          return urlParam;
        }
      }
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored === 'en' || stored === 'ur') {
        return stored;
      }
    } catch (e) {
      // LocalStorage access restricted (e.g. sandboxed iframe or private browsing)
    }
    return 'ur';
  }

  function writeStoredLanguage(lang) {
    try {
      localStorage.setItem(STORAGE_KEY, lang);
    } catch (e) {
      // Ignore storage errors
    }
  }

  function t(key, params) {
    const dict = DICTIONARIES[currentLang] || DICTIONARIES.ur;
    let text = dict[key] || DICTIONARIES.en[key] || key;
    if (params && typeof params === 'object') {
      Object.keys(params).forEach(k => {
        text = text.replace(new RegExp('\\{' + k + '\\}', 'g'), params[k]);
      });
    }
    return text;
  }

  function applyLanguage(lang) {
    if (lang !== 'ur' && lang !== 'en') lang = 'ur';
    currentLang = lang;
    writeStoredLanguage(lang);

    // Update root document lang and direction
    // NOTE: UI button positions remain fixed in LTR layout; only text translates and styles
    const root = document.documentElement;
    root.setAttribute('lang', lang);
    root.setAttribute('dir', 'ltr');

    // Update Language Toggle button
    const toggleBtn = document.getElementById('lang-toggle-btn');
    if (toggleBtn) {
      const fullSpan = toggleBtn.querySelector('.lang-toggle-label-full');
      const shortSpan = toggleBtn.querySelector('.lang-toggle-label-short');

      if (fullSpan) fullSpan.textContent = t('lang_toggle.label');
      if (shortSpan) shortSpan.textContent = lang === 'en' ? 'اردو' : 'ENG';

      toggleBtn.setAttribute('title', t('lang_toggle.title'));
      toggleBtn.setAttribute('aria-pressed', lang === 'en' ? 'true' : 'false');
    }

    // Translate DOM elements with data-i18n
    document.querySelectorAll('[data-i18n]').forEach(el => {
      const key = el.getAttribute('data-i18n');
      if (key) {
        const translated = t(key);
        if (el.hasAttribute('data-i18n-html')) {
          el.innerHTML = translated;
        } else {
          el.textContent = translated;
        }
      }
    });

    // Translate placeholders with data-i18n-placeholder
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
      const key = el.getAttribute('data-i18n-placeholder');
      if (key) {
        el.setAttribute('placeholder', t(key));
      }
    });

    // Translate titles with data-i18n-title
    document.querySelectorAll('[data-i18n-title]').forEach(el => {
      const key = el.getAttribute('data-i18n-title');
      if (key) {
        el.setAttribute('title', t(key));
      }
    });

    // Translate dynamic prompt attributes with data-i18n-prompt
    document.querySelectorAll('[data-i18n-prompt]').forEach(el => {
      const key = el.getAttribute('data-i18n-prompt');
      if (key) {
        el.setAttribute('data-prompt', t(key));
      }
    });

    // Toggle font-ur class on prompt chips according to active language
    document.querySelectorAll('.prompt-chip').forEach(el => {
      if (lang === 'ur') {
        el.classList.add('font-ur');
      } else {
        el.classList.remove('font-ur');
      }
    });

    // Notify registered listeners
    listeners.forEach(fn => {
      try {
        fn(currentLang);
      } catch (e) {
        console.error('Error in i18n listener:', e);
      }
    });
  }

  function toggleLanguage() {
    const nextLang = currentLang === 'ur' ? 'en' : 'ur';
    applyLanguage(nextLang);
    return nextLang;
  }

  function onLanguageChange(fn) {
    if (typeof fn === 'function') {
      listeners.push(fn);
    }
  }

  // Initialize on load
  function init() {
    currentLang = readStoredLanguage();
    applyLanguage(currentLang);

    const toggleBtn = document.getElementById('lang-toggle-btn');
    if (toggleBtn) {
      toggleBtn.addEventListener('click', toggleLanguage);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Export globally
  window.HisaabI18n = {
    t: t,
    getLanguage: function () { return currentLang; },
    setLanguage: applyLanguage,
    toggleLanguage: toggleLanguage,
    onLanguageChange: onLanguageChange,
    DICTIONARIES: DICTIONARIES
  };
  window.t = t;

})();
