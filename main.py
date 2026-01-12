from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import PlainTextResponse
import numpy as np
import numpy_financial as npf
import json

app = FastAPI()

API_KEY = "meadows_internal_key_123"


# -------------------- DEBUG ENDPOINT (PLAIN TEXT) --------------------
@app.post("/debug", response_class=PlainTextResponse)
def debug(payload: dict):
    return json.dumps(payload)


# -------------------- HELPERS --------------------
def parse_array(value, name):
    if value is None:
        raise HTTPException(status_code=400, detail=f"{name} is missing or null")

    if isinstance(value, list):
        try:
            return [float(x) for x in value]
        except Exception:
            raise HTTPException(status_code=400, detail=f"{name} list contains non-numeric values")

    if isinstance(value, str):
        try:
            parts = [
                p.replace(" ", "").replace("\u00A0", "")
                for p in value.split(",")
                if p.strip() != ""
            ]
            return [float(p) for p in parts]
        except Exception:
            raise HTTPException(status_code=400, detail=f"{name} contains non-numeric values")

    raise HTTPException(status_code=400, detail=f"{name} must be a list or comma-separated string")


# -------------------- MAIN ENDPOINT --------------------
@app.post("/run-analysis")
def run_analysis(payload: dict, x_api_key: str = Header(None)):

    print("RAW PAYLOAD:", payload)

    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    cashflows = parse_array(payload.get("cashflow", {}).get("cashflows"), "cashflows")
    ebitda = parse_array(payload.get("ebitda", {}).get("ebitda"), "ebitda")
    dev_costs = parse_array(payload.get("development_costs", {}).get("costs"), "development_costs")

    debt = payload.get("debt_terms", {})

    loan = float(debt.get("loan_amount", 0))
    rate = float(debt.get("interest_rate", 0))
    tenor = int(debt.get("tenor_months", 0))
    io_period = int(debt.get("interest_only_period", 0))
    repayment = str(debt.get("repayment", "")).lower()
    amort_rate = float(debt.get("amortisation_rate", 0))
    availability = int(debt.get("availability_period", 0))
    drawdown_mode = str(debt.get("drawdown_mode", "upfront")).lower()

    roll_up = debt.get("interest_roll_up") is True

    arrangement_fee = float(debt.get("arrangement_fee", 0))
    exit_fee = float(debt.get("exit_fee", 0))

    dscr_threshold = debt.get("dscr_threshold")
    icr_threshold = debt.get("icr_threshold")
    debt_ebitda_max = debt.get("debt_to_ebitda_max")
    ltv_threshold = debt.get("ltv_threshold")
    ltc_threshold = debt.get("ltc_threshold")
    property_value = debt.get("property_value")

    if len(cashflows) < tenor:
        raise HTTPException(status_code=400, detail="Insufficient cashflows")

    balances = []
    interest = []
    principal = []
    drawdowns = []

    dscrs = []
    icrs = []
    debt_ebitdas = []
    ltcs = []
    ltvs = []

    balance = 0.0
    undrawn = loan
    monthly_rate = rate / 12

    for t in range(1, tenor + 1):

        draw = 0.0
        if drawdown_mode == "upfront" and t == 1:
            draw = loan
        elif drawdown_mode == "liquidity" and t <= availability:
            if cashflows[t - 1] < 0 and undrawn > 0:
                draw = min(abs(cashflows[t - 1]), undrawn)

        undrawn -= draw
        balance += draw
        drawdowns.append(draw)
        balances.append(balance)

        int_t = balance * monthly_rate
        interest.append(int_t)

        princ = 0.0
        if t > io_period:
            if repayment == "bullet":
                princ = 0.0
            elif repayment == "amortising_straight_line":
                princ = loan / (tenor - io_period)
            elif repayment == "amortising_reducing_balance":
                princ = balance * amort_rate / 12

        if t == tenor:
            princ = balance

        principal.append(princ)
        balance -= princ

        if roll_up:
            balance += int_t

        debt_service = int_t + princ
        dscrs.append(cashflows[t - 1] / debt_service if debt_service > 0 else None)

        if ebitda:
            icrs.append(ebitda[t - 1] / int_t if int_t > 0 else None)
            debt_ebitdas.append(balance / ebitda[t - 1] if ebitda[t - 1] > 0 else None)

        if dev_costs:
            cum_cost = sum(dev_costs[:t])
            ltcs.append(balance / cum_cost if cum_cost > 0 else None)

        if property_value:
            ltvs.append(balance / float(property_value))

    valid = lambda x: [i for i in x if i is not None]

    wal = (
        sum(p * (i + 1) for i, p in enumerate(principal)) / sum(principal)
        if sum(principal) > 0 else tenor
    )

    lender_cfs = []
    for i in range(tenor):
        cf = -drawdowns[i]
        if not roll_up:
            cf += interest[i]
        cf += principal[i]
        lender_cfs.append(cf)

    lender_cfs[0] -= arrangement_fee
    lender_cfs[-1] += exit_fee

    lender_irr = npf.irr(lender_cfs)

    total_interest = sum(interest)
    avg_balance = np.mean(balances)

    wacd = (total_interest + arrangement_fee + exit_fee) / (avg_balance * (tenor / 12))
    avg_interest = total_interest / tenor

    return {
        "min_dscr": min(valid(dscrs)) if valid(dscrs) else None,
        "min_icr": min(valid(icrs)) if valid(icrs) else None,
        "max_debt_to_ebitda": max(valid(debt_ebitdas)) if valid(debt_ebitdas) else None,
        "max_ltc": max(valid(ltcs)) if valid(ltcs) else None,
        "max_ltv": max(valid(ltvs)) if valid(ltvs) else None,
        "weighted_average_life_months": wal,
        "lender_irr": lender_irr,
        "weighted_avg_cost_of_debt": wacd,
        "average_interest": avg_interest,
        "ending_balance": balance,
    }
