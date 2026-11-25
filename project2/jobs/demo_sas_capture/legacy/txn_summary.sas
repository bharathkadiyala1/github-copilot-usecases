/* Mock SAS job (reference only - it is NOT run by the tool). */
data work.txn;
  infile "&input" dsd firstobs=2;
  input txn_id $ account_id $ amount currency $;
  if missing(amount) then delete;                 /* blank amounts dropped */
  if currency = "USD" then amount_cad = round(amount * 1.35, 0.01);
  else amount_cad = amount;
run;
proc sql;
  create table work.summary as
  select account_id, count(*) as txn_count, sum(amount_cad) as total_cad format=12.2
  from work.txn group by account_id order by account_id;
quit;
proc export data=work.summary outfile="&output" dbms=csv replace; run;
