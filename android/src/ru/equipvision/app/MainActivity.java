package ru.equipvision.app;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Bundle;
import android.os.Environment;
import android.view.View;
import android.view.WindowManager;
import android.view.inputmethod.EditorInfo;
import android.webkit.CookieManager;
import android.webkit.PermissionRequest;
import android.webkit.SslErrorHandler;
import android.webkit.URLUtil;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.List;

/**
 * The EquipVision web client in a WebView. The application holds no logic of its own: it asks
 * for the address of the server once, opens it, and passes the camera and the microphone to the page.
 */
public class MainActivity extends Activity {
    private static final String PREFS = "equipvision";
    private static final String KEY_URL = "server_url";
    private static final int REQUEST_MEDIA = 1;

    private WebView web;
    private View setup;
    private EditText address;
    private TextView error;
    String serverHost;           // host of the saved address; nothing else gets the camera
    String trustedHost;          // host whose certificate the user accepted in this run
    PermissionRequest pending;   // the page's request waiting for the Android permission dialog

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);   // an inspection takes minutes
        setContentView(R.layout.main);
        web = findViewById(R.id.web);
        setup = findViewById(R.id.setup);
        address = findViewById(R.id.address);
        error = findViewById(R.id.error);

        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);    // the camera preview starts by itself
        settings.setSupportZoom(false);
        CookieManager.getInstance().setAcceptCookie(true);
        web.setWebViewClient(new Client(this));
        web.setWebChromeClient(new Chrome(this));
        web.setDownloadListener((url, agent, disposition, mime, length) -> download(url, agent, disposition, mime));

        findViewById(R.id.open).setOnClickListener(view -> open(address.getText().toString()));
        address.setOnEditorActionListener((view, action, event) -> {
            if (action != EditorInfo.IME_ACTION_GO) return false;
            open(address.getText().toString());
            return true;
        });

        String saved = prefs().getString(KEY_URL, null);
        if (saved == null) showSetup(null);
        else open(saved);
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, MODE_PRIVATE);
    }

    void showSetup(String message) {
        web.setVisibility(View.GONE);
        setup.setVisibility(View.VISIBLE);
        String saved = prefs().getString(KEY_URL, null);
        if (saved != null && address.getText().length() == 0) address.setText(saved);
        error.setText(message == null ? "" : message);
        error.setVisibility(message == null ? View.GONE : View.VISIBLE);
    }

    /** Checks the address, remembers it and loads the page. */
    private void open(String text) {
        String url = text.trim();
        if (url.isEmpty()) {
            showSetup(getString(R.string.error_address));
            return;
        }
        if (!url.contains("://")) url = "https://" + url;
        Uri uri = Uri.parse(url);
        if (uri.getHost() == null) {
            showSetup(getString(R.string.error_address));
            return;
        }
        if (!"https".equalsIgnoreCase(uri.getScheme())) {
            showSetup(getString(R.string.error_https));
            return;
        }
        serverHost = uri.getHost();
        prefs().edit().putString(KEY_URL, url).apply();
        setup.setVisibility(View.GONE);
        web.setVisibility(View.VISIBLE);
        web.loadUrl(url);
    }

    boolean isServer(String url) {
        String host = url == null ? null : Uri.parse(url).getHost();
        return host != null && host.equalsIgnoreCase(serverHost);
    }

    // Static nested classes with an explicit reference to the activity: an inner class would get a compiler-made
    // constructor parameter that the dex compiler of Build-Tools 34 cannot read from Java 21 class files.
    private static final class Client extends WebViewClient {
        private final MainActivity app;

        Client(MainActivity app) {
            this.app = app;
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            if (app.isServer(request.getUrl().toString())) return false;
            try {                                             // anything else is not ours: the browser opens it
                app.startActivity(new Intent(Intent.ACTION_VIEW, request.getUrl()));
            } catch (RuntimeException ignored) {
                // no application for this link
            }
            return true;
        }

        @Override
        public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError sslError) {
            String host = Uri.parse(sslError.getUrl()).getHost();
            if (host == null || !host.equalsIgnoreCase(app.serverHost)) {
                handler.cancel();
                return;
            }
            if (host.equalsIgnoreCase(app.trustedHost)) {
                handler.proceed();
                return;
            }
            // A server in the plant network has a self-signed certificate: the user decides, once per run.
            new AlertDialog.Builder(app)
                    .setTitle(R.string.cert_title)
                    .setMessage(app.getString(R.string.cert_message, host))
                    .setCancelable(false)
                    .setPositiveButton(R.string.cert_continue, (dialog, which) -> {
                        app.trustedHost = host;
                        handler.proceed();
                    })
                    .setNegativeButton(R.string.cert_cancel, (dialog, which) -> {
                        handler.cancel();
                        app.showSetup(app.getString(R.string.cert_rejected));
                    })
                    .show();
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError failure) {
            if (request.isForMainFrame()) {
                app.showSetup(app.getString(R.string.error_load, request.getUrl().toString(), failure.getDescription()));
            }
        }
    }

    private static final class Chrome extends WebChromeClient {
        private final MainActivity app;

        Chrome(MainActivity app) {
            this.app = app;
        }

        @Override
        public void onPermissionRequest(PermissionRequest request) {
            app.runOnUiThread(() -> {
                if (!app.isServer(request.getOrigin().toString())) {
                    request.deny();
                    return;
                }
                List<String> missing = new ArrayList<>();
                for (String resource : request.getResources()) {
                    String permission = androidPermission(resource);
                    if (permission != null && app.checkSelfPermission(permission) != PackageManager.PERMISSION_GRANTED) {
                        missing.add(permission);
                    }
                }
                if (missing.isEmpty()) {
                    app.answer(request);
                } else {
                    app.pending = request;
                    app.requestPermissions(missing.toArray(new String[0]), REQUEST_MEDIA);
                }
            });
        }

        @Override
        public void onPermissionRequestCanceled(PermissionRequest request) {
            if (app.pending == request) app.pending = null;
        }
    }

    static String androidPermission(String resource) {
        if (PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(resource)) return Manifest.permission.CAMERA;
        if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(resource)) return Manifest.permission.RECORD_AUDIO;
        return null;
    }

    /** Gives the page the camera and the microphone it asked for, as far as Android allowed them. */
    void answer(PermissionRequest request) {
        List<String> granted = new ArrayList<>();
        for (String resource : request.getResources()) {
            String permission = androidPermission(resource);
            if (permission != null && checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED) {
                granted.add(resource);
            }
        }
        if (granted.isEmpty()) request.deny();
        else request.grant(granted.toArray(new String[0]));
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(code, permissions, results);
        if (code == REQUEST_MEDIA && pending != null) {
            PermissionRequest request = pending;
            pending = null;
            answer(request);
        }
    }

    /** Reports are files: the system download manager saves them, with the session cookie of the page. */
    private void download(String url, String agent, String disposition, String mime) {
        try {
            String name = URLUtil.guessFileName(url, disposition, mime);
            DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url));
            String cookie = CookieManager.getInstance().getCookie(url);
            if (cookie != null) request.addRequestHeader("Cookie", cookie);
            request.addRequestHeader("User-Agent", agent);
            request.setMimeType(mime);
            request.setTitle(name);
            request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name);
            ((DownloadManager) getSystemService(DOWNLOAD_SERVICE)).enqueue(request);
            Toast.makeText(this, getString(R.string.download_started, name), Toast.LENGTH_LONG).show();
        } catch (RuntimeException failure) {
            Toast.makeText(this, R.string.download_failed, Toast.LENGTH_LONG).show();
        }
    }

    @Override
    public void onBackPressed() {
        if (setup.getVisibility() == View.VISIBLE) {
            finish();
            return;
        }
        String[] items = {getString(R.string.menu_reload), getString(R.string.menu_change), getString(R.string.menu_exit)};
        new AlertDialog.Builder(this)
                .setItems(items, (dialog, which) -> {
                    if (which == 0) web.reload();
                    else if (which == 1) showSetup(null);
                    else finish();
                })
                .show();
    }

    @Override
    protected void onPause() {
        super.onPause();
        web.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        web.onResume();
    }

    @Override
    protected void onDestroy() {
        web.destroy();
        super.onDestroy();
    }
}
