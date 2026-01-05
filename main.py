from fastapi import FastAPI, HTTPException, Header
import numpy as np
import numpy_financial as npf

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

    loan_amount = float(debt.get("loan_amount", 0))
    interest_rate = float(debt.get("interest_rate", 0))
    tenor_months = int(debt.get("tenor_months", 0))
    repayment_type = str(debt.get("repayment_type", "")).lower()

    # Optional / extended inputs (safe defaults)
    arrangement_fee = float(debt.get("arrangement_fee", 0))
    exit_fee = float(debt.get("exit_fee", 0))
    interest_only_period = int(debt.get("interest_only_period", 0))
    amortisation_rate = debt.get("amortisation_rate")  # may be None
    dscr_threshold = debt.get("dscr_threshold")  # may be None

    # ---- Parse cashflows string into list[float] ----
    cashflows = None

    if isinstance(raw_cashflows, str):
        parts = [p for p in raw_cashflows.split(",") if p.strip() != ""]
        try:
            cashflows = [float(p) for p in parts]
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="cashflows string contains non-numeric values",
            )
    elif isinstance(raw_cashflows, list):
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

    # ---- Validation ----
    if not cashflows:
        raise HTTPException(status_code=400, detail="cashflows missing or empty")

    if tenor_months <= 0:
        raise HTTPException(status_code=400, detail="tenor_months must be > 0")

    if len(cashflows) < tenor_months:
        raise HTTPException(
            status_code=400,
            detail="cashflows length must be >= tenor_months",
        )

    # ---- Build internal monthly schedule (arrays stay INTERNAL) ----
    balance = loan_amount
    monthly_rate = interest_rate / 12.0

    balances = []
    interest_payments = []
    principal_payments = []
    dscrs = []

    for month in range(1, tenor_months + 1):
        balances.append(balance)

        interest = balance * monthly_rate
        interest_payments.append(interest)

        cfads = cashflows[month - 1]
        dscrs.append(cfads / interest if interest > 0 else None)

        # Principal logic
        if month <= interest_only_period:
            principal = 0.0
        elif repayment_type == "bullet":
            principal = 0.0
        elif amortisation_rate:
            principal = balance * float(amortisation_rate)
        else:
            principal = loan_amount / tenor_months

        principal_payments.append(principal)
        balance -= principal

    # ---- Existing metrics ----
    min_dscr = min(d for d in dscrs if d is not None)
    average_interest = sum(interest_payments) / len(interest_payments)

    # ---- NEW METRICS ----

    # Weighted Average Life (months)
    principal_array = np.array(principal_payments)
    time_array = np.arange(1, len(principal_array) + 1)

    if principal_array.sum() > 0:
        weighted_average_life_months = (
            (principal_array * time_array).sum() / principal_array.sum()
        )
    else:
        weighted_average_life_months = tenor_months

    # Lender IRR
    lender_cashflows = [-loan_amount - arrangement_fee]

    for i in range(len(interest_payments)):
        lender_cashflows.append(
            interest_payments[i] + principal_payments[i]
        )

    lender_cashflows[-1] += exit_fee

    try:
        lender_irr = npf.irr(lender_cashflows)
    except Exception:
        lender_irr = None

    # Weighted Average Cost of Debt (annualised)
    total_interest = sum(interest_payments)
    total_fees = arrangement_fee + exit_fee

    weighted_avg_cost_of_debt = (
        (total_interest + total_fees)
        / (loan_amount * (tenor_months / 12))
    )

    # Cash-on-cash yield (annualised)
    avg_balance = np.mean(balances)
    cash_on_cash_yield = (
        (sum(interest_payments) / tenor_months * 12) / avg_balance
        if avg_balance > 0
        else None
    )

    # DSCR headroom
    if dscr_threshold is not None:
        dscr_headroom = min_dscr - float(dscr_threshold)
        dscr_pass = min_dscr >= float(dscr_threshold)
    else:
        dscr_headroom = None
        dscr_pass = None

    # ---- Flat, Bubble-safe response ----
    return {
        "min_dscr": min_dscr,
        "ending_balance": balance,
        "average_interest": average_interest,

        "weighted_avg_cost_of_debt": weighted_avg_cost_of_debt,
        "weighted_average_life_months": weighted_average_life_months,
        "lender_irr": lender_irr,
        "cash_on_cash_yield": cash_on_cash_yield,

        "dscr_headroom": dscr_headroom,
        "dscr_pass": dscr_pass,
    }

