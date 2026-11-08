# Project Rules & Guidelines for HisaabAI

## 🚨 ABSOLUTE HARD CONSTRAINT: NO PUSH TO MAIN / NO EC2 DEPLOYMENT
- **NEVER EVER push code to the `main` branch or trigger any deployment to AWS EC2 unless the user EXPLICITLY and UNAMBIGUOUSLY requests it in the current prompt (e.g., "push to main", "deploy to ec2", "merge to main").**
- All active development, bug fixes, features, experiments, and commits MUST strictly stay on the `dev` branch or local workspace.
- Never assume a fix should be pushed to `main` or deployed to production automatically.
- Merging to `main`, pushing to `origin/main`, or invoking AWS SSM / EC2 deploy scripts requires direct, explicit user confirmation each time.

## 🔒 Security & Architecture Standards
- Zero hardcoded mock credentials, fake transactions, or mock user IDs.
- All ledger data, daily sales (Galla), and Udhaar Khata must strictly read and write to the database.
- Enforce Jameel Noori Nastaleeq font and proper RTL styling for all Urdu content.
- Adhere strictly to the verification test suite before any commit.
