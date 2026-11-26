/* Tiny, safe markdown-to-HTML for Copilot replies. Supports headings, lists, tables, code blocks,
   quotes, rules, **bold**, *italic*, `code`. Everything is HTML-escaped first. */
function escapeHtml(s){return String(s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function md(src){
  var lines=String(src||'').replace(/\r/g,'').split('\n'), out=[], i=0;
  function inline(t){return escapeHtml(t).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>').replace(/(^|[^*])\*([^*\s][^*]*)\*/g,'$1<i>$2</i>')}
  function cells(l){return l.trim().replace(/^\|/,'').replace(/(^|[^\\])\|$/,'$1').split(/(?<!\\)\|/).map(function(c){return c.trim().replace(/\\\|/g,'|')})}
  function blockStart(l){return /^\s*(```|#{1,6}\s|[-*]\s|\d+[.)]\s|>|\|)/.test(l)||/^(-{3,}|\*{3,})\s*$/.test(l.trim())}
  while(i<lines.length){
    var l=lines[i], m;
    if(/^\s*```/.test(l)){var code=[];i++;while(i<lines.length&&!/^\s*```/.test(lines[i])){code.push(lines[i]);i++}i++;out.push('<pre><code>'+escapeHtml(code.join('\n'))+'</code></pre>');continue}
    if(!l.trim()){i++;continue}
    if((m=l.match(/^(#{1,6})\s+(.*)$/))){var n=Math.min(m[1].length+1,6);out.push('<h'+n+'>'+inline(m[2])+'</h'+n+'>');i++;continue}
    if(/^(-{3,}|\*{3,})\s*$/.test(l.trim())){out.push('<hr>');i++;continue}
    if(/^\s*\|/.test(l)&&i+1<lines.length&&/^\s*\|?\s*:?-{2,}/.test(lines[i+1])){
      var head=cells(l);i+=2;var rows=[];while(i<lines.length&&/\|/.test(lines[i])&&lines[i].trim()){rows.push(cells(lines[i]));i++}
      out.push('<table><tr>'+head.map(function(c){return '<th>'+inline(c)+'</th>'}).join('')+'</tr>'+rows.map(function(r){return '<tr>'+r.map(function(c){return '<td>'+inline(c)+'</td>'}).join('')+'</tr>'}).join('')+'</table>');continue}
    if(/^\s*[-*]\s+/.test(l)||/^\s*\d+[.)]\s+/.test(l)){
      var ordered=/^\s*\d+[.)]\s+/.test(l), items=[];
      while(i<lines.length&&(/^\s*[-*]\s+/.test(lines[i])||/^\s*\d+[.)]\s+/.test(lines[i]))){
        var ind=lines[i].match(/^\s*/)[0].length;items.push('<li style="margin-left:'+Math.min(ind,8)*6+'px">'+inline(lines[i].replace(/^\s*([-*]|\d+[.)])\s+/,''))+'</li>');i++}
      out.push((ordered?'<ol>':'<ul>')+items.join('')+(ordered?'</ol>':'</ul>'));continue}
    if(/^>/.test(l)){var q=[];while(i<lines.length&&/^>/.test(lines[i])){q.push(lines[i].replace(/^>\s?/,''));i++}out.push('<blockquote>'+inline(q.join(' '))+'</blockquote>');continue}
    var para=[];while(i<lines.length&&lines[i].trim()&&!(para.length&&blockStart(lines[i]))){para.push(lines[i]);i++}
    out.push('<p>'+inline(para.join(' '))+'</p>');
  }
  return out.join('\n');
}
