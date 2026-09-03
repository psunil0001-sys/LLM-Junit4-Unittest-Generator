package {package}

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.drawerlayout.widget.DrawerLayout
import androidx.fragment.app.FragmentContainerView
import com.google.android.material.navigation.NavigationView
import {r_import}
import dagger.hilt.android.AndroidEntryPoint

/** androidTest Hilt host with NavHostFragment for navigation-tagged fragments. */
@AndroidEntryPoint
class HiltNavTestActivity : AppCompatActivity(), MainActivityDelegate {

    private lateinit var drawerLayout: DrawerLayout
    private lateinit var navigationView: NavigationView
    private lateinit var fragmentContainerView: FragmentContainerView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        drawerLayout = DrawerLayout(this)
        navigationView = NavigationView(this)
        fragmentContainerView = FragmentContainerView(this)
        setContentView(R.layout.hilt_nav_test_activity)
    }

    override fun getDrawerLayout(): DrawerLayout = drawerLayout
    override fun getNavigationView(): NavigationView = navigationView
    override fun getFragmentContainerView(): FragmentContainerView = fragmentContainerView
    override fun navigateToLogBookPage() = Unit
    override fun navigateToManagedTripPage() = Unit
    override fun navigateToSettingsPage() = Unit
    override fun navigateToExportPage() = Unit
}
