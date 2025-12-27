from fastapi import FastAPI, HTTPException, Header

app = FastAPI()

# ---- API KEY (hard-coded for now) ----
API_KEY = "meadows_internal_key_123"


@app.post("/run-analysis")
def run_analysis(payload: dict, x_api_key: str = Header(None)):
    # ---- API key check ----
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # ---- Extract inputs ----
    cashflow_block = payload.get("cashflow", {})
    raw_cashflows = cashflow_block.get("cashflows")  # expect string from Bubble

    debt = payload.get("debt_terms", {})
    loan_amount = debt.get("loan_amount")
    interest_rate = debt.get("interest_rate")
    tenor_months = debt.get("tenor_months")
    repayment_type = debt.get("repayment_type", "").lower()

    # ---- Parse cashflows string into list[float] ----
    # Accept either:
    # - a comma-separated string: "300000,320000,340000"
    # - or a proper list: [300000, 320000, 340000] (for Swagger/Postman)
    cashflows = None

    if isinstance(raw_cashflows, str):
        # from Bubble: "300000,320000,340000"
        parts = [p for p in raw_cashflows.split(",") if p.strip() != ""]
        try:
            cashflows = [float(p) for p in parts]
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="cashflows string contains non-numeric values",
            )
    elif isinstance(raw_cashflows, list):
        # from Swagger/Postman: [300000, 320000, 340000]
        try:
            cashflows = [float(x) for x in raw_cashflows]
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail="cashflows list contains non-numeric values",
            )
    else:
        raise HTTPException(
            status_code=400,
            detail="cashflows must be a comma-separated string or a list",
        )

    # ---- Minimal validation (unchanged semantics) ----
    if not cashflows or not isinstance(cashflows, list):
        raise HTTPException(status_code=400, detail="cashflows missing or empty")

    if not tenor_months or tenor_months <= 0:
        raise HTTPException(status_code=400, detail="tenor_months must be > 0")

    if len(cashflows) < tenor_months:
        raise HTTPException(
            status_code=400,
            detail="cashflows length must be >= tenor_months",
        )

    # ---- Simple debt logic (same as your original) ----
    balance = float(loan_amount)
    monthly_rate = float(interest_rate) / 12.0

    interest_payments = []
    dscrs = []

    for i in range(tenor_months):
        interest = balance * monthly_rate
        cfads = float(cashflows[i])

        dscr = cfads / interest if interest > 0 else None

        principal = (
            0.0
            if repayment_type == "bullet"
            else float(loan_amount) / float(tenor_months)
        )
        balance -= principal

        interest_payments.append(interest)
        dscrs.append(dscr)

    min_dscr = min(d for d in dscrs if d is not None)

    return {
        "min_dscr": min_dscr,
        "ending_balance": balance,
        "average_interest": sum(interest_payments) / len(interest_payments),
    }
