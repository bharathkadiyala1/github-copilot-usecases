/* txn_summary.sas - daily account summary
   REFERENCE ONLY: shown to Copilot for analysis. It is not run here.
   SAS ROUND(x, .01) rounds half away from zero. */
%let infile = /data/in/transactions.dat;

data work.txn;
    infile "&infile." lrecl=72 pad;
    input @1  txn_id      $8.
          @9  account_id  $10.
          @19 date_txt    $8.
          @27 txn_type    $6.
          @33 amount_txt  $12.
          @45 currency    $3.
          @48 channel     $6.;
    line_no = _n_;
    channel = upcase(strip(channel));
    if channel = '' then channel = 'UNKNOWN';
    txn_date = input(date_txt, ?? yymmdd8.);
    amount   = input(amount_txt, ?? 12.2);
    length reason $12;
    if txn_id = '' then reason = 'MISSING_ID';
    else if account_id = '' then reason = 'BAD_ACCOUNT';
    else if txn_date = . then reason = 'BAD_DATE';
    else if txn_type not in ('DEP','INT','XFRIN','WDR','WIRE','POS','XFROUT') then reason = 'BAD_TYPE';
    else if amount = . or amount <= 0 then reason = 'BAD_AMOUNT';
    else if currency not in ('CAD','USD') then reason = 'BAD_CURRENCY';
run;

proc sort data=work.txn(where=(reason='')) out=work.good nodupkey; by txn_id; run;

data work.priced;
    set work.good;
    if currency = 'USD' then cad = round(amount * 1.35, .01); else cad = amount;
    fee = 0;
    if txn_type = 'WDR' and channel = 'ATM' and cad > 500 then fee = 2.00;
    if txn_type = 'WIRE' then fee = max(5, min(25, round(cad * 0.001, .01)));
    credit = (txn_type in ('DEP','INT','XFRIN'));
    month_end = (intnx('day', txn_date, 1) = intnx('month', txn_date, 1, 'b'));
run;

proc sql;
    create table work.summary as
    select account_id, count(*) as txn_count,
           sum(cad * credit) as credits, sum(cad * (1 - credit)) as debits, sum(fee) as fees,
           max(txn_date) as last_txn_date format=yymmdd10.,
           sum(month_end) as month_end_txn_count, sum(channel = 'ATM') as atm_txn_count
    from work.priced group by account_id order by account_id;
quit;

proc export data=work.summary outfile="/data/out/summary.csv" dbms=csv replace; run;
