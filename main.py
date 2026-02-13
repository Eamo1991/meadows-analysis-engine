from fastapi import FastAPI, HTTPException, Header
import numpy as np
import numpy_financial as npf

app = FastAPI()
API_KEY = "meadows_internal_key_123"


# ---------------- SAFE HELPERS ----------------

def safe_float(value, default=0.0):
    try:
        if value in [None, ""]:
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value, default=0):
    try:
        if value in [None, ""]:
            return default
        return int(float(value))
    except Exception:
        return default


def parse_array_soft(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [safe_float(x, 0.0) for x in value]
    if isinstance(value, str):
        parts = [
            p.replace(" ", "").replace("\u00A0", "")
            for p in value.split(",")
            if p.strip() != ""
        ]
        return [safe_float(p, 0.0) for p in parts]
    return []


def pad_array(arr, length):
    if len(arr) >= length:
        return arr[:length]
    return arr + [0.0] * (length - len(arr))


def safe_div(numerator, denominator):
    try:
        if denominator == 0:
            return None
        return numerator / denominator
    except Exception:
        return None


def sanitize(value):
    if value is None:
        return None
    if isinstance(value, float):
        if np.isnan(value) or np.isinf(value):
            return None
    return value


# ---------------- MAIN ENDPOINT ----------------

@app.post("/run-analysis")
def run_analysis(payload: dict, x_api_key: str = Header(None)):

    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    debug_mode = payload.get("debug", True)

    cashflows = parse_array_soft(payload.get("cashflow", {}).get("cashflows"))
    ebitda = parse_array_soft(payload.get("ebitda", {}).get("ebitda"))
    dev_costs = parse_array_soft(payload.get("development_costs", {}).get("costs"))

    debt = payload.get("debt_terms", {})

    loan = safe_float(debt.get("loan_amount"))
    rate = safe_float(debt.get("interest_rate"))
    tenor = safe_int(debt.get("tenor_months"))
    io_period = safe_int(debt.get("interest_only_period"))
    repayment = str(debt.get("repayment", "")).lower()
    amort_rate = safe_float(debt.get("amortisation_rate"))
    availability = safe_int(debt.get("availability_period"))
    drawdown_mode = str(debt.get("drawdown_mode", "upfront")).lower()
    roll_up = debt.get("interest_roll_up") is True
    arrangement_fee = safe_float(debt.get("arrangement_fee"))
    exit_fee = safe_float(debt.get("exit_fee"))
    property_value = safe_float(debt.get("property_value"))

    if tenor <= 0:
        tenor = max(len(cashflows), 1)

    if io_period > tenor:
        io_period = tenor

    cashflows = pad_array(cashflows, tenor)
    ebitda = pad_array(ebitda, tenor)
    dev_costs = pad_array(dev_costs, tenor)

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

    for t in range(tenor):

        draw = 0.0

        if drawdown_mode == "upfront" and t == 0:
            draw = loan

        elif drawdown_mode == "liquidity" and t < availability:
            if cashflows[t] < 0 and undrawn > 0:
                draw = min(abs(cashflows[t]), undrawn)

        undrawn -= draw
        balance += draw
        drawdowns.append(draw)

        int_t = balance * monthly_rate
        interest.append(int_t)

        princ = 0.0

        if t >= io_period:
            if repayment == "amortising_straight_line" and tenor > io_period:
                princ = loan / (tenor - io_period)
            elif repayment == "amortising_reducing_balance":
                princ = balance * amort_rate / 12

        if t == tenor - 1:
            princ = balance

        principal.append(princ)
        balance -= princ

        if roll_up:
            balance += int_t

        balances.append(balance)

        debt_service = int_t + princ

        dscrs.append(safe_div(cashflows[t], debt_service))
        icrs.append(safe_div(ebitda[t], int_t))
        debt_ebitdas.append(safe_div(balance, ebitda[t]))
        cum_cost = sum(dev_costs[: t + 1])
        ltcs.append(safe_div(balance, cum_cost))
        ltvs.append(safe_div(balance, property_value))

    total_principal = sum(principal)

    wal = (
        sum(p * (i + 1) for i, p in enumerate(principal)) / total_principal
        if total_principal > 0
        else tenor
    )

    lender_cfs = []

    for i in range(tenor):
        cf = -drawdowns[i]
        if not roll_up:
            cf += interest[i]
        cf += principal[i]
        lender_cfs.append(cf)

    if lender_cfs:
        lender_cfs[0] -= arrangement_fee
        lender_cfs[-1] += exit_fee

    try:
        lender_irr = npf.irr(lender_cfs)
    except Exception:
        lender_irr = None

    total_interest = sum(interest)
    avg_balance = np.mean(balances) if balances else 0.0

    wacd = safe_div(
        total_interest + arrangement_fee + exit_fee,
        avg_balance * (tenor / 12) if tenor > 0 else 0,
    )

    avg_interest = safe_div(total_interest, tenor)

    response = {
        "min_dscr": sanitize(min([x for x in dscrs if x is not None], default=None)),
        "min_icr": sanitize(min([x for x in icrs if x is not None], default=None)),
        "max_debt_to_ebitda": sanitize(max([x for x in debt_ebitdas if x is not None], default=None)),
        "max_ltc": sanitize(max([x for x in ltcs if x is not None], default=None)),
        "max_ltv": sanitize(max([x for x in ltvs if x is not None], default=None)),
        "weighted_average_life_months": sanitize(wal),
        "lender_irr": sanitize(lender_irr),
        "weighted_avg_cost_of_debt": sanitize(wacd),
        "average_interest": sanitize(avg_interest),
        "ending_balance": sanitize(balance),
    }

    if debug_mode:
        response["debug"] = {
            "rate": rate,
            "monthly_rate": monthly_rate,
            "loan": loan,
            "drawdowns": drawdowns,
            "balances": balances,
            "interest": interest,
            "principal": principal,
            "lender_cashflows": lender_cfs,
        }

    return response

