package {package}

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import dagger.hilt.android.AndroidEntryPoint

/** androidTest Hilt host without MainActivityDelegate — negative onAttach path. */
@AndroidEntryPoint
class PlainHiltActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
    }
}
