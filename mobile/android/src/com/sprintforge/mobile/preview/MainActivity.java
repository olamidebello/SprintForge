package com.sprintforge.mobile.preview;
import android.app.Activity;
import android.os.Bundle;
import android.content.Intent;
import android.net.Uri;
import android.webkit.*;
import android.widget.*;
public class MainActivity extends Activity {
 private WebView web; private TextView status;
 private static final String HOME="https://sprintforge.netwizzard.tech/";
 private boolean internal(Uri u){return "https".equals(u.getScheme())&&"sprintforge.netwizzard.tech".equalsIgnoreCase(u.getHost())&&(u.getPort()==-1||u.getPort()==443)&&u.getUserInfo()==null;}
 private void external(Uri u){if(!("https".equals(u.getScheme())||"mailto".equals(u.getScheme())))return;try{startActivity(new Intent(Intent.ACTION_VIEW,u));}catch(Exception e){status.setText("Open this link in your browser.");}}
 public void onCreate(Bundle state){super.onCreate(state);LinearLayout layout=new LinearLayout(this);layout.setOrientation(1);layout.setOnApplyWindowInsetsListener((v,i)->{v.setPadding(i.getSystemWindowInsetLeft(),i.getSystemWindowInsetTop(),i.getSystemWindowInsetRight(),i.getSystemWindowInsetBottom());return i;});
 LinearLayout bar=new LinearLayout(this);for(String name:new String[]{"Home","Reload","Browser"}){Button b=new Button(this);b.setText(name);b.setOnClickListener(v->{if(name.equals("Home"))web.loadUrl(HOME);else if(name.equals("Reload"))web.reload();else external(Uri.parse(HOME));});bar.addView(b,new LinearLayout.LayoutParams(0,-2,1));}layout.addView(bar);status=new TextView(this);status.setText("SprintForge preview · Internet required");layout.addView(status);web=new WebView(this);layout.addView(web,new LinearLayout.LayoutParams(-1,0,1));setContentView(layout);
 WebSettings s=web.getSettings();s.setJavaScriptEnabled(true);s.setDomStorageEnabled(true);s.setAllowFileAccess(false);s.setAllowContentAccess(false);s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);s.setGeolocationEnabled(false);CookieManager.getInstance().setAcceptThirdPartyCookies(web,false);
 web.setWebViewClient(new WebViewClient(){public boolean shouldOverrideUrlLoading(WebView v,WebResourceRequest r){Uri u=r.getUrl();if(internal(u))return false;if(r.isForMainFrame())external(u);return true;}public void onPageFinished(WebView v,String url){CookieManager.getInstance().flush();status.setText("SprintForge preview · Online workspace");}public void onReceivedError(WebView v,WebResourceRequest r,WebResourceError e){if(r.isForMainFrame())status.setText("Connection failed. Check internet and tap Reload.");}public void onReceivedSslError(WebView v,SslErrorHandler h,android.net.http.SslError e){h.cancel();status.setText("TLS certificate error. Use Browser after the site is repaired.");}});
 web.setWebChromeClient(new WebChromeClient(){public boolean onCreateWindow(WebView v,boolean dialog,boolean gesture,android.os.Message message){status.setText("Open legal pages and external links with Browser.");return false;}});web.setDownloadListener((url,ua,disposition,mime,size)->external(Uri.parse(url)));web.loadUrl(HOME);}
 public void onBackPressed(){if(web.canGoBack())web.goBack();else super.onBackPressed();}
 protected void onDestroy(){web.stopLoading();web.destroy();super.onDestroy();}
}
